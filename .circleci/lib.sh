# CircleCI build environment looks like it has a zillion and a half cores.
# Don't let Nix autodetect this high core count because it blows up memory
# usage and fails the test run.  Pick a number of cores that suits the build
# environment we're paying for (the free one!).
DEPENDENCY_CORES=3

# Once dependencies are built, we can allow some more concurrency for our own
# test suite.
UNITTEST_CORES=8

# Run a command, enabling cache writes to cachix if possible.  The command is
# accepted as a variable number of positional arguments (like argv).
function cache_if_able() {
    # Dump some info about our build environment.
    describe_build

    if is_cache_writeable; then
	# If the cache is available we'll use it.  This lets fork owners set
	# up their own caching if they want.
	echo "Cachix credentials present; will attempt to write to cache."

	# The `cachix watch-exec ...` does our cache population.  When it sees
	# something added to the store (I guess) it pushes it to the named
	# cache.
	cachix watch-exec "${CACHIX_NAME}" -- "$@"
    else
	if is_cache_required; then
	    echo "Required credentials (CACHIX_AUTH_TOKEN) are missing."
	    return 1
	else
	    echo "Cachix credentials missing; will not attempt cache writes."
	    "$@"
	fi
    fi
}

function is_cache_writeable() {
    # We can only *push* to the cache if we have a CACHIX_AUTH_TOKEN.  in-repo
    # jobs will get this from CircleCI configuration but jobs from forks may
    # not.
    [ -v CACHIX_AUTH_TOKEN ]
}

function is_cache_required() {
    # If we're building in tahoe-lafs/tahoe-lafs then we must use the cache.
    # If we're building anything from a fork then we're allowed to not have
    # the credentials.
    is_upstream
}

# Return success if the origin of this build is the tahoe-lafs/tahoe-lafs
# repository itself (and so we expect to have cache credentials available),
# failure otherwise.
#
# See circleci.txt for notes about how this determination is made.
function is_upstream() {
    # CIRCLE_PROJECT_USERNAME is set to the org the build is happening for.
    # If a PR targets a fork of the repo then this is set to something other
    # than "tahoe-lafs".
    [ "$CIRCLE_PROJECT_USERNAME" == "tahoe-lafs" ] &&

	# CIRCLE_BRANCH is set to the real branch name for in-repo PRs and
	# "pull/NNNN" for pull requests from forks.
	#
	# CIRCLE_PULL_REQUESTS is set to a comma-separated list of the full
	# URLs of the PR pages which share an underlying branch, with one of
	# them ended with that same "pull/NNNN" for PRs from forks.
	! any_element_endswith "/$CIRCLE_BRANCH" "," "$CIRCLE_PULL_REQUESTS"
}

# Return success if splitting $3 on $2 results in an array with any element
# that ends with $1, failure otherwise.
function any_element_endswith() {
    suffix=$1
    shift

    sep=$1
    shift

    haystack=$1
    shift

    IFS="${sep}" read -r -a elements <<< "$haystack"
    for elem in "${elements[@]}"; do
	if endswith "$suffix" "$elem"; then
	    return 0
	fi
    done
    return 1
}

# Return success if $2 ends with $1, failure otherwise.
function endswith() {
    suffix=$1
    shift

    haystack=$1
    shift

    case "$haystack" in
	*${suffix})
	    return 0
	    ;;

	*)
	    return 1
	    ;;
    esac
}

function describe_build() {
    echo "Building PR for user/org: ${CIRCLE_PROJECT_USERNAME}"
    echo "Building branch: ${CIRCLE_BRANCH}"
    if is_upstream; then
	echo "Upstream build."
    else
	echo "Non-upstream build."
    fi
    if is_cache_required; then
	echo "Cache is required."
    else
	echo "Cache not required."
    fi
    if is_cache_writeable; then
	echo "Cache is writeable."
    else
	echo "Cache not writeable."
    fi
}

# Inspect the flake input metadata for an input of a given name and return the
# revision at which that input is pinned.  If the input does not exist then
# return garbage (probably "null").
read_input_revision() {
    input_name=$1
    shift

    nix flake metadata --json | jp --unquoted 'locks.nodes."'"$input_name"'".locked.rev'
}

# Return a flake reference that refers to a certain revision of nixpkgs.  The
# certain revision is the revision to which the specified input is pinned.
nixpkgs_flake_reference() {
    input_name=$1
    shift

    echo "github:NixOS/nixpkgs?rev=$(read_input_revision $input_name)"
}
