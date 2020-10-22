# given a PR number, get all contributers and the summary from
# GitHub's API

import sys
import json
import base64

from twisted.internet.task import react
from twisted.internet.defer import inlineCallbacks, returnValue

import treq

base_pr_url = "https://api.github.com/repos/tahoe-lafs/tahoe-lafs/pulls/{}"
ignore_handles = ('codecov-io', )


def _find_pull_request_numbers():
    """
    This returns a list of Pull Request numbers that are
    interesting. It first assumes any command-line arguments are PR
    numbers. Failing that, it reads stdin and looks for works starting
    with 'PR'
    """
    if len(sys.argv) < 2:
        data = sys.stdin.read()
        if not len(data):
            print("put some PR numbers on the command-line")
            raise SystemExit(1)
        else:
            all_prs = set()
            for word in data.split():
                word = word.strip()
                if word.startswith('PR'):
                    all_prs.add(word[2:])
            all_prs = list(all_prs)
            print("Found {} PRs in stdin text".format(len(all_prs)))
    else:
        all_prs = sys.argv[1:]
    return all_prs


def _read_github_token(fname='token'):
    """
    read a secret github token; a 'token' file contains two lines:
    username, github token.

    If the token can't be found, SystemExit is raised
    """
    try:
        with open(fname, 'r') as f:
            data = f.read().strip()
            username, token = data.split('\n', 1)
    except (IOError, EnvironmentError) as e:
        print("Couldn't open or parse 'token' file: {}".format(e))
        raise SystemExit(1)
    except ValueError:
        print("'token' should contain two lines: username, github token")
        raise SystemExit(1)
    return username, token


def _initialize_headers(username, token):
    """
    Create the base headers for all requests.

    :return dict: the headers dict
    """
    return {
        "User-Agent": "treq",
        "Authorization": "Basic {}".format(base64.b64encode("{}:{}".format(username, token))),
    }


@inlineCallbacks
def _report_authors(data, headers):
    print("Commits:")
    commits_resp = yield treq.get(data['commits_url'], headers=headers)
    commits_data = yield commits_resp.text()
    commits = json.loads(commits_data)
    authors = set()
    for commit in commits:
        if commit['author'] is None:
            print("  {}: no author!".format(commit['sha']))
        else:
            author = commit['author']['login']
            print("  {}: {}".format(commit['sha'], author))
            if author not in ignore_handles:
                authors.add(author)
    returnValue(authors)


@inlineCallbacks
def _report_helpers(data, headers):
    helpers = set()
    print("Comments:")
    comments_resp = yield treq.get(data['comments_url'], headers=headers)
    comments_data = yield comments_resp.text()
    comments = json.loads(comments_data)
    for comment in comments:
        author = comment['user']['login']
        if author not in ignore_handles:
            helpers.add(author)
        print("  {}: {}".format(author, comment['body'].replace('\n', ' ')[:60]))
    returnValue(helpers)

@inlineCallbacks
def _request_pr_information(username, token, headers, all_prs):
    """
    Download PR information from GitHub.

    :return dict: mapping PRs to a 2-tuple of "contributers" and
        "helpers" to the PR. Contributers are nicks of people who
        commited to the PR, and "helpers" either reviewed or commented
        on the PR.
    """
    pr_info = dict()

    for pr in all_prs:
        print("Fetching PR{}".format(pr))
        resp = yield treq.get(
            base_pr_url.format(pr),
            headers=headers,
        )
        raw_data = yield resp.text()
        data = json.loads(raw_data)

        code_handles = yield _report_authors(data, headers)
        help_handles = yield _report_helpers(data, headers)

        pr_info[pr] = (
            code_handles,
            help_handles - help_handles.intersection(code_handles),
        )
    returnValue(pr_info)


#async def main(reactor):
@inlineCallbacks
def main(reactor):
    """
    Fetch Pull Request (PR) information from GitHub.

    Either pass a list of PR numbers on the command-line, or pipe text
    containing references like: "There is a PR123 somewhere" from
    which instances of "PRxxx" are extrated. From GitHub's API we get
    all author information and anyone who disucced the PR and print a
    summary afterwards.

    You need a 'token' file containing two lines: your username, and
    access token (get this from the GitHub Web UI).
    """

    username, token = _read_github_token()
    pr_info = yield _request_pr_information(
        username, token,
        _initialize_headers(username, token),
        _find_pull_request_numbers(),
    )

    unique_handles = set()
    for pr, (code_handles, help_handles) in sorted(pr_info.items()):
        coders = ', '.join('`{}`_'.format(c) for c in code_handles)
        helpers = ', '.join('`{}`_'.format(c) for c in help_handles)
        if helpers:
            print("`PR{}`_: {} (with {})".format(pr, coders, helpers))
        else:
            print("`PR{}`_: {}".format(pr, coders))
        for h in code_handles.union(help_handles):
            unique_handles.add(h)

    for pr in sorted(pr_info.keys()):
        print(".. _PR{}: https://github.com/tahoe-lafs/tahoe-lafs/pull/{}".format(pr, pr))
    for h in sorted(unique_handles):
        print(".. _{}: https://github.com/{}".format(h, h))


if __name__ == "__main__":
    #react(lambda r: ensureDeferred(main(r)))
    react(main)
