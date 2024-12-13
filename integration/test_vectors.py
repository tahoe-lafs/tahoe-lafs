"""
Verify certain results against test vectors with well-known results.
"""

from __future__ import annotations

from functools import partial
from typing import AsyncGenerator, Iterator
from itertools import starmap, product

from attrs import evolve

from pytest import mark
from pytest_twisted import ensureDeferred

from . import vectors
from .vectors import parameters
from .util import upload
from .grid import Client

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
    await alice.reconfigure_zfec(
        reactor, (1, case.params.required, case.params.total), case.convergence, case.segment_size)

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
    space = starmap(
        # segment_size could be a parameter someday but it's not easy to vary
        # using the Python implementation so it isn't one for now.
        partial(vectors.Case, segment_size=parameters.SEGMENT_SIZE),
        product(
            parameters.ZFEC_PARAMS,
            parameters.CONVERGENCE_SECRETS,
            parameters.OBJECT_DESCRIPTIONS,
            parameters.FORMATS,
        ),
    )
    iterresults = generate(reactor, request, alice, space)

    results = []
    async for result in iterresults:
        # Accumulate the new result
        results.append(result)
        # Then rewrite the whole output file with the new accumulator value.
        # This means that if we fail partway through, we will still have
        # recorded partial results -- instead of losing them all.
        vectors.save_capabilities(results)

async def generate(
        reactor,
        request,
        alice: Client,
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
        await alice.reconfigure_zfec(
            reactor,
            (happy, case.params.required, case.params.total),
            case.convergence,
            case.segment_size
        )

        # Give the format a chance to make an RSA key if it needs it.
        case = evolve(case, fmt=case.fmt.customize())
        cap = upload(alice.process, case.fmt, case.data)
        yield case, cap
