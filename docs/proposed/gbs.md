# GBS Internals Design

This is the corresponding document to the HTTP Storage Node Protocol, except focusing on actual implementation details within the context of the existing `tahoe-lafs` codebase.

## Question 1: What programming language to use?

Options include:

1. `LANG-PY23`: Current intersection of Python 2 and Python 3.
   This has maximum compatibility.
2. `LANG-PY3`: Python 3 only.
   Means we can use newer language features more easily, but makes merging back in more tricky.
3. `LANG-PY3-RUST`: Some combination of Python 3 and Rust exposed via `PyO3`.
   The benefit of using Rust is ability to share code with future implementations in other languages, much like C, but less likelihood of buffer overflows and other security problems, as well as performance improvements.
   In practice this seems like unnecessary complexity; if it turns out performance is an issue this option can be revisited.

### Recommendation

Start with Python-only, but beyond that TBD, need to:

1. Discuss timeline of dropping Python 2.
2. See which versions are supported by 3rd-party dependencies.
   Which brings us to the next question—


## Question 2: What criteria to use for new dependencies?

### Security

As part of a network protocol that is exposed publicly, there are security considerations involved in choice of dependency libraries.

Consider CBOR support, for example.
There appear to be the following main options:

1. `cbor2`: Has many users, current version is Python 3 only but there is older version that supports Python 2. Has both Python code and optional giant pile of C code.
2. `cbors`: Python 3 only.
   Written in Rust so inherently much more secure than C code, much faster than a pure-Python implementation, but has _far_ fewer users.
   On the third hand, the amount of new code in it is tiny, most of the heavy lifting is done by broadly used existing Rust libraries.

### TBD: Any other criteria?

### Recommendation

In practice decisions would have to be made on a case-by-case basis, but some guidelines would be good.

TBD.
I would personally choose `cbors` over `cbor2`, assuming extensions aren't needed.


