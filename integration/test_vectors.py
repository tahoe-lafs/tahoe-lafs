"""
Verify certain results against test vectors with well-known results.
"""

from __future__ import annotations

from typing import AsyncGenerator, Iterator
from itertools import starmap, product
from yaml import safe_dump

from attrs import evolve

from pytest import mark
from pytest_twisted import ensureDeferred

from twisted.python.filepath import FilePath

from . import vectors
from .vectors import parameters
from .util import reconfigure, upload, TahoeProcess

@mark.parametrize('convergence', parameters.CONVERGENCE_SECRETS)
def test_convergence(convergence):
    """
    Convergence secrets are 16 bytes.
    """
    assert isinstance(convergence, bytes), "Convergence secret must be bytes"
    assert len(convergence) == 16, "Convergence secret must by 16 bytes"


@mark.slow
@mark.parametrize('case,expected', vectors.capabilities.items())
@ensureDeferred
async def test_capability(reactor, request, alice, case, expected):
    """
    The capability that results from uploading certain well-known data
    with certain well-known parameters results in exactly the previously
    computed value.
    """
    # rewrite alice's config to match params and convergence
    await reconfigure(reactor, request, alice, (1, case.params.required, case.params.total), case.convergence)

    # upload data in the correct format
    actual = upload(alice, case.fmt, case.data)

    # compare the resulting cap to the expected result
    assert actual == expected


@ensureDeferred
async def skiptest_generate(reactor, request, alice):
    """
    This is a helper for generating the test vectors.

    You can re-generate the test vectors by fixing the name of the test and
    running it.  Normally this test doesn't run because it ran once and we
    captured its output.  Other tests run against that output and we want them
    to run against the results produced originally, not a possibly
    ever-changing set of outputs.
    """
    space = starmap(vectors.Case, product(
        parameters.ZFEC_PARAMS,
        parameters.CONVERGENCE_SECRETS,
        parameters.OBJECT_DESCRIPTIONS,
        parameters.FORMATS,
    ))
    iterresults = generate(reactor, request, alice, space)

    # Update the output file with results as they become available.
    results = []
    async for result in iterresults:
        results.append(result)
        write_results(vectors.DATA_PATH, results)

def write_results(path: FilePath, results: list[tuple[vectors.Case, str]]) -> None:
    """
    Save the given results.
    """
    path.setContent(safe_dump({
        "version": vectors.CURRENT_VERSION,
        "vector": [
            {
                "convergence": vectors.encode_bytes(case.convergence),
                "format": {
                    "kind": case.fmt.kind,
                    "params": case.fmt.to_json(),
                },
                "sample": {
                    "seed": vectors.encode_bytes(case.seed_data.seed),
                    "length": case.seed_data.length,
                },
                "zfec": {
                    "segmentSize": parameters.SEGMENT_SIZE,
                    "required": case.params.required,
                    "total": case.params.total,
                },
                "expected": cap,
            }
            for (case, cap)
            in results
        ],
    }).encode("ascii"))

async def generate(
        reactor,
        request,
        alice: TahoeProcess,
        cases: Iterator[vectors.Case],
) -> AsyncGenerator[[vectors.Case, str], None]:
    """
    Generate all of the test vectors using the given node.

    :param reactor: The reactor to use to restart the Tahoe-LAFS node when it
        needs to be reconfigured.

    :param request: The pytest request object to use to arrange process
        cleanup.

    :param format: The name of the encryption/data format to use.

    :param alice: The Tahoe-LAFS node to use to generate the test vectors.

    :param case: The inputs for which to generate a value.

    :return: The capability for the case.
    """
    # Share placement doesn't affect the resulting capability.  For maximum
    # reliability of this generator, be happy if we can put shares anywhere
    happy = 1
    for case in cases:
        await reconfigure(
            reactor,
            request,
            alice,
            (happy, case.params.required, case.params.total),
            case.convergence
        )

        # Give the format a chance to make an RSA key if it needs it.
        case = evolve(case, fmt=case.fmt.customize())
        cap = upload(alice, case.fmt, case.data)
        yield case, cap
