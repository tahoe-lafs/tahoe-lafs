
import re

umidmap = {
    'lp1vaQ': 'download-not-enough-shares',
    '3uuBUQ': 'download-connection-lost-in-get-buckets',
    'LkD9Pw': 'user-incident-button',
}

def classify_incident(trigger):
    m = trigger.get('message', '')
    f = trigger.get('format', '')

    umid_value = umidmap.get(trigger.get('umid',''), None)
    if umid_value:
        return umid_value

    if re.search(r"^they had shares .* that we didn't know about$", m):
        # Publish._got_write_answer
        return "mutable-publish-surprise-shares"

    if m.startswith("error during query"):
        # there are a couple of different places that can generate this
        # message (the result of cut-and-paste error-handling), so it isn't
        # clear which is which

        if re.search(r'mutable/servermap\.py.*_do_query', m):
            # servermap.ServermapUpdater._query_failed()
            where = "mapupdate"
        elif re.search(r'mutable/retrieve\.py.*_got_results_one_share', m):
            where = "retrieve"
        else:
            where = "unknown"

        if ("Calling Stale Broker" in m and "DeadReferenceError" in m):
            # a storage server went offline while we were talking to it (or
            # because the client was shut off in the middle of an operation)
            what = "lost-server"
        elif "IOError" in m:
            what = "ioerror"
        elif ("UncoordinatedWriteError" in m and
              "someone wrote to the data since we read the servermap" in m):
            what = "uncoordinated-write-error"
        elif "ConnectionLost" in m:
            what = "lost-server"
        else:
            what = "unknown"

        return "mutable-" + where + "-query-" + what

    if (f.startswith("ran out of peers:") and
        "have" in trigger and "need" in trigger):
        return "mutable-retrieve-failure"
    if m.startswith("invalid privkey from "):
        # TODO: a UCW causes this, after the prefix has changed. Compare the
        # prefix before trying to validate the privkey, to avoid the
        # duplicate error.
        return "mutable-mapupdate-bad-privkey"

    if trigger.get('facility', '') == "tahoe.introducer":
        if (trigger.get('isError', False)
            and "ConnectionDone" in str(trigger.get('failure',''))):
            return "introducer-lost-connection"
        if "Initial Introducer connection failed" in m:
            return "introducer-connection-failed"

    return None
