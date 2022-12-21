# Run a command, enabling cache writes to cachix if possible.  The command is
# accepted as a variable number of positional arguments (like argv).
function cache_if_able() {
    # The `cachix watch-exec ...` does our cache population.  When it sees
    # something added to the store (I guess) it pushes it to the named cache.
    #
    # We can only *push* to it if we have a CACHIX_AUTH_TOKEN, though.
    # in-repo jobs will get this from CircleCI configuration but jobs from
    # forks may not.
    if [ -v CACHIX_AUTH_TOKEN ]; then
	echo "Cachix credentials present; will attempt to write to cache."
	cachix watch-exec "${CACHIX_NAME}" -- "$@"
    else
	# If we're building a from a forked repository then we're allowed to
	# not have the credentials (but it's also fine if the owner of the
	# fork supplied their own).
	if [ "${CIRCLE_PR_REPONAME}" == "https://github.com/tahoe-lafs/tahoe-lafs" ]; then
	    echo "Required credentials (CACHIX_AUTH_TOKEN) are missing."
	    return 1
	else
	    echo "Cachix credentials missing; will not attempt cache writes."
	    "$@"
	fi
    fi
}
