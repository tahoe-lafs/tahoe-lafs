"""Benchmarks for minimal `tahoe` CLI interactions."""

def test_cp_one_file(client_node):
    """
    Upload a file with tahoe cp and then download it, measuring the latency of
    both operations.
    """
