# GBS Internals Design

This is the corresponding document to the HTTP Storage Node Protocol, except focusing on actual implementation details within the context of the existing `tahoe-lafs` codebase.

This is a design document, where design is used as a verb.

## Resources

* [The HTTP spec](http-storage-node-protocol.rst)
* https://github.com/LeastAuthority/haskell-tahoe-lafs-storage-server is a sketch in Haskell of the proposed protocol.

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


## Question 3: How should support for two protocols be implemented in code structure?

While the transition from old to new protocol is happening, the codebase will need support both protocols.
Beyond having two code paths, this requires some thought about the internal APIs will work to support this.

### Design alternatives

#### `INTERNAL-CLIENT-API-SEPARATE`: Two completely distinct APIs

There are two distinct storage APIs available to Python code, one for Foolscap and one for GBS, and code using the storage APIs is required to support both.
This require touching every single client of the current internal storage API and making it support two different APIs.

It's unclear to me how much code this would actually touch.
Command-line tools, for example, wouldn't need to change since they use Tahoe-LAFS's external high-level "filesystem" HTTP API, rather than the internal storage APIs.

TODO: Audit the code/talk to experts to clarify.


#### `INTERNAL-CLIENT-API-NEW-EMULATES-OLD`: Implement facade around GBS that implements Foolscap-like API

Even though GBS is quite different protocol, one could in theory create a GBS-based Python API that emulates the existing Foolscap protocol/API underneath (e.g. `RIStorageServer` interface).
Python code talking to storage server would therefore mostly be unchanged.

Upside: Don't need to change much in existing code that uses storage client API.
Less chance of breakage.

Downside: Stuck with old interfaces even once old protocol is done, but that could be fixed with second pass of refactoring.

#### `INTERNAL-CLIENT-API-OLD-EMULATES-NEW`: Implement facade around Foolscap that implements GBS-like API

1. Come up improved, GBS-y interface.
2. Build facade that makes Foolscap storage client API that look like GBS client API.
3. Update all code that currently uses Foolscap storage client API to use the new GBS-y interface.

Upside: Code starts using improved API from the beginning.

Downside: All code using `RIStorageServer` etc. needs to be updated.
More chance of bugs.

### Discussion

The first option is clearly possible.
It's not clear which of the other two options is actually feasible, the semantics may be too different.


## Question 4: Can we create a (somewhat) formal protocol spec of the HTTP API?

There are various tools for specifying HTTP APIs.
This is useful for documentation, and also for validation.

For the HTTP level, options includes:

* OpenAPI (Swagger)
* ...

For the CBOR/JSON records.

* For the records, CDDL is apparently the schema language of choice for CBOR; it also supports JSON.
  There is a Rust implementation which could be wrapped for Python, but support is in general not very broad (but might suffice).
* JSON Schema tools _might_ work with CBOR, not sure how bytes are handled.
