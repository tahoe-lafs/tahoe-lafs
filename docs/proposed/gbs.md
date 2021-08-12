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
   The main issue is _existing_ modules needs to be changed to support new protocol.
   If you start using Python 3-only code, those modules might fail or even stop importing on Python 2.
3. `LANG-PY3-RUST-CORE`: A core library for the storage protocol written in Rust, with Python just being thin bindings, with `PyO3` (which supports PyPy too via `cpyext`: https://pyo3.rs/v0.14.1/building_and_distribution/pypy.html).
   The benefit of using Rust is ability to share code with future implementations in other languages, much like C, but less likelihood of buffer overflows and other security problems, as well as performance improvements.
4. `LANG-PY3-RUST-PERFORMANCE`: Still a Python project, but Rust is used when performance is a bottleneck.
5. `LANG-PY3-NEW-MODULES`: New modules are Python 3-only, but existing modules stick to intersection of Python 2 + 3 that is currently in use, so as not to break Python 2.

### `LANG-PY3-RUST-CORE` Discussion

#### Pros

There is an interest in supporting iOS/Android/browser.
Rust would make these easier; maybe.

1. Seems like using Rust in Android/iOS is possible.
2. Unclear how concurrency requirements feed in to it (no-I/O approach? just block?).
3. Browser is somewhat iffier insofar as `wasm` is a quite different computing environment.

#### Cons

No one else knows Rust.

The protocol isn't that complex, doing a no-I/O model would be necessary to get this to integrate everywhere, so probably not worth the complexity.

### Recommendation

`LANG-PY3`, on the presumption that Python 2 support can continue in a long-lived branch, rather than blocking new development on main branch.

## Question 2: What criteria to use for new dependencies?

### Security

As part of a network protocol that is exposed publicly, there are security considerations involved in choice of dependency libraries.

Consider CBOR support, for example.
There appear to be the following main options:

1. `cbor2`: Has many users, current version is Python 3 only but there is older version that supports Python 2. Has both Python code and optional giant pile of C code.
2. `cbors`: Python 3 only.
   Written in Rust so inherently much more secure than C code, much faster than a pure-Python implementation, but has _far_ fewer users.
   On the third hand, the amount of new code in it is tiny, most of the heavy lifting is done by broadly used existing Rust libraries.

### Portability

Tahoe-LAFS wants to run on Linux/macOS/Windows, at least.
As such:

1. A pure-Python package is great.
2. A package with compiled code should have binary wheels on PyPI for all three.

### Recommendation

In practice decisions would have to be made on a case-by-case basis, but basic criteria are as above:

1. Works.
2. Maintained.
3. Does not use unsafe language (C/C++) if there are alternatives.
4. Available for easy installation (no compiler required) on Linux/macOS/Windows.
5. Compatible license.

## Question 3: How should support for two protocols be implemented in code structure, on the client side?

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

Looking through the code, it seems that storage clients interact with the server using a `RIStorageServer`.
Much of its interface is simple methods, pass in simple objects and get back simple objects.

The two exceptions are:

1. Reading a bucket, which involves a `RIBucketReader` that has a simple `read()` method, and a simple `advise_corrupt_share` method.
2. When buckets are allocated, one gets back an object structure that has a dictionary with `RIBucketWriter`.
   The bucket writers have simple `write()/close()/abort()` methods.

This isn't _that_ object-oriented of an API.

...

The first option is clearly possible.
It's not clear which of the other two options is actually feasible, the semantics may be too different.

TODO braindump what we discussed.

```
[2021-08-04 14:46:38] <itamarst> so there are 3 proposed models (there might be more, if you can think of any)
[2021-08-04 14:46:51] <exarkun> It _might_ make sense to consider 2 instances of question 3, though perhaps we'll come to the same answer for each
[2021-08-04 14:46:51] <itamarst> 1. completely separate interfaces, all clients need to support both 
[2021-08-04 14:46:56] <exarkun> client, server
[2021-08-04 14:47:11] <itamarst> 2. new GBS emulates current RIStorageServer
[2021-08-04 14:47:16] <exarkun> because there's little to no implementation overlap right now.  they're basically two separate jobs.
[2021-08-04 14:47:31] <itamarst> 3. create RIStorageServer2, create facade for foolscap protocol that provides RISTroageServer2
[2021-08-04 14:47:36] <itamarst> oh
[2021-08-04 14:47:37] <itamarst> I only thought about this from client perspective
[2021-08-04 14:47:40] <itamarst> I'll add another question
[2021-08-04 14:48:36] <itamarst> so focusing on client perspective for now (I restricted scope of question 3, question 4 is now server)
[2021-08-04 14:48:36] <exarkun> Okay, so starting with the client version of this then
[2021-08-04 14:48:49] <itamarst> 1. did I miss any other options?
[2021-08-04 14:49:02] <itamarst> 2. how many users of RIStorageServer are there?
[2021-08-04 14:49:51] <exarkun> The Tahoe project's official position is that Tahoe does not have a public Python API except (maybe) for allmydata.testing.  So officially we can break anyone's code if they're using RIStorageServer.
[2021-08-04 14:50:12] <exarkun> In practice, I only know of one other project that uses RIStorageServer.  The good or bad news is that it's one of my projects.
[2021-08-04 14:51:14] <exarkun> Inside Tahoe itself, I don't know how you'd draw coherent lines between different users.  So I guess 1, smeared out across a large area of the codebase. :)
[2021-08-04 14:51:43] <exarkun> I guess you could say there are three?  CHK, SDMF, and MDMF... except I think that's not actually a useful answer
[2021-08-04 14:52:20] <itamarst> that's a start
[2021-08-04 14:52:27] <itamarst> and what I meant, yeah
[2021-08-04 14:52:38] <exarkun> Or, technically, I guess there is one RIStorageServer user - IStorageServer.  And then there are some users of IStorageServer.  But I dunno if that is an important distinction, probably not.
[2021-08-04 14:52:53] <itamarst> so
[2021-08-04 14:52:58] <exarkun> (it's only really about calling convention - callRemote vs normal method calls)
[2021-08-04 14:53:18] <itamarst> two most realistic options are "GBS implements RIStorageServer" and "Foolscap implements RIStorageServer2"
[2021-08-04 14:53:22] <itamarst> oh I forgot about callRemote
[2021-08-04 14:53:23] <itamarst> ugh
[2021-08-04 14:53:36] <exarkun> I think that all of the callRemote usage is hidden by IStorageServer
[2021-08-04 14:53:49] <exarkun> ie, there are not random storage-related Foolscap callRemotes happening throughout the codebase
[2021-08-04 14:54:07] <itamarst> ah, ok
[2021-08-04 14:54:30] <exarkun> So with "GBS implements RIStorageServer" we get to leave all of the RIStorageServer-using code alone
[2021-08-04 14:54:35] <itamarst> so amend the above to "GBS implements IStorageServer", "Foolscap implements RIStorageServer2 facade pointing at IStorageServer (or maybe RIStorageServer)"
[2021-08-04 14:54:39] <exarkun> And just provide a new RIStorageServer implementation that makes HTTP calls instead
[2021-08-04 14:54:45] <itamarst> oh
[2021-08-04 14:54:51] <itamarst> so there's actually ... four variants
[2021-08-04 14:55:20] <exarkun> Okay I gotta get a piece of paper
[2021-08-04 14:55:23] <itamarst> or three? "GBS implements IStorageServer", "GBS implements RIStorageServer", "Foolscap path implements IStorageServer2 as facade"
[2021-08-04 14:55:39] <itamarst> and I guess facade can be to either IStorageServer or RIStorageServer
[2021-08-04 14:55:55] <itamarst> so four
[2021-08-04 14:56:30] <exarkun> IStorageServer is sorta _meant_ to be the thing you could swap out.
[2021-08-04 14:58:20] <exarkun> "GBS implements RIStorageServer" is technically a possible option but I think we should scratch it
[2021-08-04 14:58:36] <exarkun> The "R" means "Foolscap thing"
[2021-08-04 14:58:47] — itamarst nods
[2021-08-04 14:58:53] <itamarst> so... IStorageServer seems under-specified
[2021-08-04 14:59:02] <exarkun> Yes, I was just noticing that
[2021-08-04 14:59:20] <exarkun> Also, even if it were fully specified, since it is a light wrapper around RIStorageServer, it is kind of not the shape you want for GBS anyway
[2021-08-04 14:59:46] <exarkun> For example, allocate_buckets returns a Foolscap RemoteReference to a container holding IStorageBucketWriter providers
[2021-08-04 14:59:49] <itamarst> like... does it return IStorageBucketReader instead of RIBucketReader?
[2021-08-04 15:00:07] <exarkun> heh.  at moment, not sure why that is IStorageBucketWriter instead of RIStorageBucketWriter.
[2021-08-04 15:00:40] <itamarst> RIStorageServer says allocate_buckets returns RIBucketWriter
[2021-08-04 15:01:00] <itamarst> there is no interface documenting that it returns RIStorageBucketWriters, but perhaps that's what IStorageServer.allocate_buckets returns
[2021-08-04 15:01:08] <exarkun> Ah yes sorry, you're right, I mixed up client and server interfaces
[2021-08-04 15:01:14] <itamarst> there is no interface documenting that it returns _IStorageBucketWriters_, but perhaps that's what IStorageServer.allocate_buckets returns
[2021-08-04 15:01:34] <exarkun> er, no, it's not even client and server, it's some other weird internal factoring choice, blech
[2021-08-04 15:01:44] <meejah> exarkun: warner was able to successfully give .. two of us access to tahoe-lafs.org stuff in Gandi, but I can't give that power onwards
[2021-08-04 15:02:22] <exarkun> meejah: Okay.  I guess the conclusion to the wormhole/DNS thing needs to be handled by one of those two people (unless we want to fix DNS access control first).
[2021-08-04 15:02:56] <exarkun> itamarst: So for "GBS implements RIStorageServer" we have several extra stateful interfaces to implement as well
[2021-08-04 15:03:16] → mayleesia joined (~mayleesia@business-90-187-246-213.pool2.vodafone-ip.de)
[2021-08-04 15:03:22] <exarkun> itamarst: I don't think they're prohibitively hard to implement.  They're mainly just about ... "simplified" ... state tracking in the client code.
[2021-08-04 15:03:38] <itamarst> I'm not sure I understand
[2021-08-04 15:03:42] ⇐ maylee quit (~mayleesia@2a02:8106:31:b200:eda4:1550:59c9:c3bc): Ping timeout: 272 seconds
[2021-08-04 15:03:47] <exarkun> For example, an RIBucketWriter is just a thing that remembers which storage index and share number you're writing to
[2021-08-04 15:03:56] <itamarst> it seems like the foolscap (RI*) interfaces are actually simpler than IStorageServer?
[2021-08-04 15:04:00] <itamarst> ah, right that
[2021-08-04 15:04:41] <exarkun> Foolscap implements it so the _server_ remembers it ... but you could just as easily have the client remember it.
[2021-08-04 15:04:49] — itamarst nods
[2021-08-04 15:04:52] <exarkun> (Or, you know, more easily)
[2021-08-04 15:05:16] <exarkun> There is something to do with security properties here to be careful of, but you don't have to be _that_ careful to get it right
[2021-08-04 15:05:45] <itamarst> so you'd have IStorageServer implementation (unchanged) -> RIStorageServer GBS code -> INativeGBSServer ---{HTTP}---> the GBS server
[2021-08-04 15:05:50] <exarkun> (like, the server can't let clients show up in the _middle_ of this protocol and just start writing anything wherever they want)
[2021-08-04 15:06:22] <itamarst> (and possibly INativeGBSServer is unnecessary in this design)
[2021-08-04 15:06:26] <exarkun> That stack would work, yea.
[2021-08-04 15:06:47] <exarkun> I was thinking you might even want to drop "RIStorageServer GBS code" and have "IStorageServer GBS code"
[2021-08-04 15:07:03] <itamarst> that's... the other design?
[2021-08-04 15:07:20] <exarkun> I don't think so
[2021-08-04 15:07:30] ⇐ mayleesia quit (~mayleesia@business-90-187-246-213.pool2.vodafone-ip.de): Ping timeout: 240 seconds
[2021-08-04 15:07:36] <exarkun> Let me rephrase
[2021-08-04 15:07:55] <exarkun> Right now there's _StorageServer, implementing IStorageServer, talking to some RIStorageServer implementation, talking Foolscap to the server
[2021-08-04 15:08:16] <itamarst> isn't the RIStorageServer implementation on the server?
[2021-08-04 15:08:22] <meejah> maylee: am I the "excellent C++ person"? (p.s. I gave it up for a reason ;)
[2021-08-04 15:08:29] — exarkun double checks the names of things
[2021-08-04 15:09:04] <itamarst> this would be easier in person
[2021-08-04 15:09:25] <exarkun> Okay, uh.  Maybe?  It's confusing because Foolscap invented RI... but doesn't really explain them or proscribe language for discussing them.
[2021-08-04 15:09:30] <exarkun> Let me rephrase again
[2021-08-04 15:09:46] — meejah still reading backlog
[2021-08-04 15:09:54] <exarkun> Right now there's _StorageServer, implementing IStorageServer, which has a RemoteReference that can be used to talk Foolscap to an RIStorageServer over the network
[2021-08-04 15:10:13] <itamarst> ok that's what I thought
[2021-08-04 15:10:53] <exarkun> So maybe I'm not sure what "RIStorageServer GBS code" actually is in your message at 15:05:46
[2021-08-04 15:11:15] <exarkun> is it a RemoteReference-alike that intercepts RIStorageServer methods and turns them into GBS calls?
[2021-08-04 15:11:22] <itamarst> yeah
[2021-08-04 15:11:38] <exarkun> Alright, got it
[2021-08-04 15:11:45] <exarkun> Then I think my alternative was this:
[2021-08-04 15:12:12] <itamarst> insofar as that API seems simpler than IStorageServer (assuming IStorageServer does IStorageBucketWriters, which are more complex than RIBucketWriter)
[2021-08-04 15:12:22] <exarkun> A new _GBSStorageServer, implementing IStorageServer, which goes right to the GBS API layer
[2021-08-04 15:12:36] <exarkun> So basically the remove RemoteReference layer
[2021-08-04 15:12:53] <exarkun> I think the simplicity is an illusion
[2021-08-04 15:13:27] <itamarst> quite possibly
[2021-08-04 15:13:38] <itamarst> ok, so that's one big picture option (or two)
[2021-08-04 15:13:46] <itamarst> the other big picture option is that we design a new interface
[2021-08-04 15:13:49] — exarkun nods
[2021-08-04 15:14:07] <itamarst> one sec, brb
[2021-08-04 15:14:19] <meejah> I see talk of CBOR; we converged on flatbuffers most-recently in autobahn/crossbar
[2021-08-04 15:15:06] <meejah> (not completely following along on the python-code RIStorageServer etc discussion but ..)
[2021-08-04 15:16:59] <meejah> cbor2 is pretty fast, but so is msgpack .. flatbuffers is a "zero parse" one, so .. "infinitely fast"? ;)
[2021-08-04 15:19:25] <itamarst> zero parse is not zero parse for python code
[2021-08-04 15:19:32] <itamarst> (back)
[2021-08-04 15:19:36] <itamarst> but we can revisit that later
[2021-08-04 15:19:50] <itamarst> so, new interface, INewStorageServer
[2021-08-04 15:20:21] <exarkun> One advantage of a new interface is that it could be less intensively Foolscap-oriented
[2021-08-04 15:20:28] <meejah> i didn't delve into flatbuffers much personally .. but, okay (re: later)
[2021-08-04 15:20:56] <exarkun> A lot of Foolscap-isms are burned into IStorageServer/RIStorageServer - a rich collection of object types, remote references every where you turn
[2021-08-04 15:20:56] <itamarst> and then it's... CHK -> INewStorageServer --{HTTP}--> GBS server
[2021-08-04 15:21:19] → maylee joined (~mayleesia@dynamic-077-013-141-040.77.13.pool.telefonica.de)
[2021-08-04 15:21:19] <itamarst> and CHK -> INewStorageServer face --->Foolscap -->IRStorageServer objects
[2021-08-04 15:21:22] <itamarst> or osmething?
[2021-08-04 15:21:43] <itamarst> this involves modifying a lot more code
[2021-08-04 15:21:48] <exarkun> A _lot_ more code
[2021-08-04 15:22:51] <exarkun> I suppose INewStorageServer might not _completely_ represent _everything_ by itself
[2021-08-04 15:23:06] <exarkun> Although ... eugh
[2021-08-04 15:23:31] <exarkun> Okay let's talk about IStorageBucketWriter again for a minute
[2021-08-04 15:23:45] <exarkun> IStorageBucketWriter.put_block doesn't translate to a distinctive foolscap call
[2021-08-04 15:24:24] <exarkun> the implemention (lives in the client) is just some simple python code that takes its arguments, a bit of state it has, and decides on a write to do
[2021-08-04 15:24:33] <exarkun> IStorageBucketWriter is for CHK btw
[2021-08-04 15:24:55] <exarkun> ("bucket" is kind of a hint telling you it's about CHK, in general, probably at least 80% accurate)
[2021-08-04 15:25:42] <exarkun> Some of IStorageBucketWriter's state is a RemoteReference to a ... uh ...
[2021-08-04 15:26:00] <exarkun> Probably an RIBucketWriter
[2021-08-04 15:26:26] <exarkun> which only has the generic write, close, abort remote methods
[2021-08-04 15:26:50] <exarkun> So approximately *all* of IStorageBucketWriter's methods turn around and do a callRemote("write", ...)
[2021-08-04 15:27:26] <exarkun> That seemed worth pointing out for some reason...
[2021-08-04 15:27:54] <itamarst> just need another layer of abstraction to hide the callRemote
[2021-08-04 15:28:07] <exarkun> Maybe just to note that any code using IStorageBucketWriter doesn't necessarily have to change?  Or you can do this in stages?
[2021-08-04 15:28:15] <itamarst> and you can have a RIBuckerWriter-y thing that doesn't require callRemote
[2021-08-04 15:28:23] <itamarst> and I don't want to rewrite all that logic
[2021-08-04 15:28:38] <exarkun> It doesn't seem strictly necessary to rewrite it
[2021-08-04 15:28:40] <exarkun> But!
[2021-08-04 15:29:19] <exarkun> A naive translation from Foolscap to GBS at this layer either preserves or maybe even worsens a performance problem with Tahoe
[2021-08-04 15:29:32] <exarkun> every write is a separate API call and round-trip
[2021-08-04 15:29:49] <exarkun> and a CHK has a minimum of ... I dunno ... 20?  40?  writes
[2021-08-04 15:30:12] <exarkun> and most of those writes are small, usually 8 bytes
[2021-08-04 15:30:40] <exarkun> So you can _also_ throw most of IStorageBucketWriter in the trash and replace it with one write of, say, 100 bytes
[2021-08-04 15:31:01] <exarkun> which simplifies the code and reduces network overhead.
[2021-08-04 15:31:11] <exarkun> possibly this is all good stuff for a _different_ project
[2021-08-04 15:32:15] <itamarst> well, you can do it in phases
[2021-08-04 15:32:27] <meejah> yeah i think we should probably resist the urge to optimize (at first) ... unless it's "easy"?
[2021-08-04 15:32:42] <itamarst> the "better communication model" bit can be done after
[2021-08-04 15:32:43] <itamarst> or
[2021-08-04 15:32:54] <exarkun> The reason I'd consider doing this earlier rather than later is the code simplification rather than the performance improvements
[2021-08-04 15:32:54] <itamarst> potentially before if it's not a restriction of foolscap APIs, just how they're currently used
[2021-08-04 15:33:12] <exarkun> Yea, that's actually a good point.  Nothing says you have to do 20 writes with Foolscap, either
[2021-08-04 15:33:13] <itamarst> i.e. changing them wouldn't change the current protocol
[2021-08-04 15:33:53] <exarkun> If fixing this first somehow saves us other effort, clearly we should do it.  It's hard to tell if it does save us effort, though.
[2021-08-04 15:34:21] <exarkun> Fixing the Foolscap implementation might make it easier to then support the same thing in GBS... except it involves fixing the Foolscap implementation which isn't super appealing
[2021-08-04 15:34:43] <exarkun> Maybe we should climb out of this rabbit hole for the moment though
[2021-08-04 15:35:56] <exarkun> I think the last high-level point made was "this involves modifying a lot more code"
[2021-08-04 15:36:27] <exarkun> The least amount of code it would involve modifying is all users of IStorageServer
[2021-08-04 15:36:38] — itamarst nods
[2021-08-04 15:37:08] <exarkun> Fortunately that's less than "all users of IStorageServer, IStorageBucketReader, and IStorageBucketWriter"
[2021-08-04 15:37:36] <exarkun> It's still probably a lot
[2021-08-04 15:37:37] <itamarst> uh
[2021-08-04 15:38:34] <itamarst> ok
[2021-08-04 15:38:39] <itamarst> the latter two being impleemntation details
[2021-08-04 15:38:50] <exarkun> not what I meant exactly
[2021-08-04 15:39:26] <exarkun> Rather, you could have INewStorageServer with methods that return IStrorageBucketReader or IStorageBucketWriter - just like IStorageServer has such methods
[2021-08-04 15:39:48] <exarkun> It would need to return different implementations than we have now - ones that do stuff with GBS
[2021-08-04 15:40:08] <itamarst> IStorageBucketWriter has a _lot_ of code though?
[2021-08-04 15:40:08] <exarkun> But then all of the CHK, SDMF, MDMF code probably keeps working as-is
[2021-08-04 15:40:26] <itamarst> most of which would be the same logic yet still need reimplementing?
[2021-08-04 15:40:26] <exarkun> Hm yea, it does.
[2021-08-04 15:41:00] <exarkun> Right.  So maybe that's not so great.  (Although a lot of that code _is_ dumb and could be thrown away, but that's the same rabbit hole)
[2021-08-04 15:41:24] <exarkun> Then use the current implementations, but do what you suggested earlier - give them a RemoteReference-alike that does GBS instead
[2021-08-04 15:41:57] <exarkun> That gets us down to a pretty small bit of code (those 3 simple methods - write, close, abort) - and all the CHK, etc code keeps working as-is
[2021-08-04 15:41:57] <itamarst> or tweak just the bits that do callRemote 
[2021-08-04 15:42:19] <exarkun> Basically I think this comes down to the idea that for each of these interfaces, we can make an independent decision about which strategy to take
[2021-08-04 15:42:41] <exarkun> a GBS-backed implementation of the current interface or a new interface with a GBS implementation and a facade over a Foolscap implementation
[2021-08-04 15:43:04] <itamarst> this is the place where I wish there were type annotations, so I could find all the users
[2021-08-04 15:43:28] <exarkun> which is nice because the good option is hard and the meh option is easy - but if we break the pieces down enough, the hard options aren't so hard anymore
[2021-08-04 15:44:10] <exarkun> Like, do the easy option everywhere first - then do a release - then delete the foolscap implementation - then iterate over the interfaces doing the hard option to each of them
[2021-08-04 15:45:19] <itamarst> yeah, so...
[2021-08-04 15:45:26] <itamarst> there's a lot of options here
[2021-08-04 15:45:36] <itamarst> so what I suggest doing is implementing a client for the protocol
[2021-08-04 15:45:56] <itamarst> and then there'll be a clear "here are the low-level methods available in new interface"
[2021-08-04 15:46:06] <itamarst> and then can revisit with one-to-one comparison with existing interfaces
[2021-08-04 15:46:55] <exarkun> I can give you the python client that servant auto-generates from the haskell definition
[2021-08-04 15:47:00] <itamarst> that might help
[2021-08-04 15:47:08] <itamarst> and I need to then dig through the code and find allthe users of these interfaces
[2021-08-04 15:47:15] <itamarst> so I can see how many places touch each
[2021-08-04 15:48:01] <itamarst> on server-side (question 4)... the APIs are simpler?
[2021-08-04 15:48:25] <itamarst> like there's IStorageBucketWriter, only the much simpler IRBucketWriter
[2021-08-04 15:48:57] <exarkun> Yea, they're vastly simpler
[2021-08-04 15:50:43] <itamarst> so I imagine they'll be some design to do, but hopefully it's less interesting exercise
[2021-08-04 15:51:10] <exarkun> Less interesting, maybe a little more sad making
[2021-08-04 15:51:25] <exarkun> Or maybe not even that sad
[2021-08-04 15:51:40] <exarkun> The is kind of an I/O abstraction, sort of, if you don't look too closely
[2021-08-04 15:51:58] <exarkun> I kind of imagine the GBS server is basically just the protocol implementation calling into those functions
[2021-08-04 15:52:09] <exarkun> but I probably forgot about some stuff
[2021-08-04 15:52:24] <itamarst> might need to add an abstraction layer I guess
[2021-08-04 15:52:30] <exarkun> maybe the server can be refactored a bit so those 43 lines are re-usable between the two implementations
[2021-08-04 15:52:51] <exarkun> How do I share some small files with someone on the internet
[2021-08-04 15:52:58] <itamarst> email?
[2021-08-04 15:53:14] <itamarst> irccloud has an "uploads" button
[2021-08-04 15:54:14] <exarkun> oh boy this generated code is not good.  oh well.
[2021-08-04 15:54:19] <exarkun> I'm emailing you
[2021-08-04 15:54:51] <itamarst> so I'm thinking...
[2021-08-04 15:55:00] <itamarst> step 1 is implement the server side?
[2021-08-04 15:55:12] <exarkun> BLOCKED FOR SECURITY REASONS!
[2021-08-04 15:55:23] <itamarst> it should be the most straightforward part
[2021-08-04 15:55:48] <itamarst> and if done right could go out in releases and even be turned on before client is ready
[2021-08-04 15:57:46] <meejah> there's "wormhole send" ;)
[2021-08-04 15:58:26] <meejah> (and "carml pastebin" if you want to go full Tor on the problem ;)
[2021-08-04 15:59:33] <meejah> at risk of a whole separate rabbit-hole, do we need to consider "helper"?
[2021-08-04 16:00:23] <meejah> (RemoteEncryptedUploadable / RIEncryptedUploadable)
[2021-08-04 16:01:17] <exarkun> oh shit
[2021-08-04 16:01:24] <exarkun> I don't know a single thing about the helper.
[2021-08-04 16:01:35] <exarkun> itamarst: I'll wormhole send if you have wormhole
[2021-08-04 16:02:25] <meejah> all i currently know/remember is the general shape: it's a thing you run on a remote server (that you trust) so that the "ZFEC expansion" happens there instead of locally (to avoid uploading more data)
[2021-08-04 16:02:29] <itamarst> hm
[2021-08-04 16:02:46] <itamarst> it's asking for a code?
[2021-08-04 16:03:09] <itamarst> meejah: could you... expand that
[2021-08-04 16:03:23] <itamarst> meejah: cause I don't understand what that means
[2021-08-04 16:03:28] <itamarst> exarkun: I do have wormhole, give me a code
[2021-08-04 16:03:38] <exarkun> see pm
[2021-08-04 16:04:15] <meejah> a "tahoe client" usually does the share-building + uploading itself, locally .. so the amoutn of uploaded data is greater than the plaintext
[2021-08-04 16:04:17] <exarkun> from a different perspective, it sounds like "another Foolscap-based protocol involved in storage operations"
[2021-08-04 16:04:28] <exarkun> _optionally_ involved in storage operations (if the client chooses to configure it)
[2021-08-04 16:04:49] <meejah> with the helper, you contact the "helper node" and upload plaintext; it does the share-building etc there (so you only upload the number of bytes in the plaintext)
[2021-08-04 16:04:58] <itamarst> 1. do people use it?
[2021-08-04 16:05:23] <meejah> seems .. unlikely? (but also: how would we know?)
[2021-08-04 16:05:27] <itamarst> 2. is it used in large scale installs, or is this more like "human uses this to talk to their personal node N which talks to larger cluster C"
[2021-08-04 16:06:03] <meejah> allmydata used it, AFAIK, but .. IMO it's only really useful if it's another computer that you (the same human) controls
[2021-08-04 16:06:38] <meejah> (because if I'm uploading all my plaintext to helper.private.storage or whatever, then it's just dropbox?)
[2021-08-04 16:06:51] <exarkun> Do you actually upload plaintext to the helper?  Not ciphertext?
[2021-08-04 16:07:01] <meejah> i'd have to check ... not 100% sure on that
[2021-08-04 16:07:18] <meejah> i am sure the "share building" happens on the helper .. but yeah maybe it gets ciphertext not plaintext
[2021-08-04 16:07:21] <exarkun> Uploading ciphertext to an untrusted node is still not _great_ but maybe it would mean the helper is not _totally_ unusable if you don't control it
[2021-08-04 16:08:04] <itamarst> this seems ... out of scope?
[2021-08-04 16:08:22] <meejah> hmm, judging by the parameter / class names involved I think it is ciphertext
[2021-08-04 16:09:06] <exarkun> GBS does not include the helper protocol
[2021-08-04 16:09:21] <meejah> yeah, possibly it's "just rabbit-hole". I guess i'm wondering if it has implications on the other decisions (which was the facade(s) face etc)
[2021-08-04 16:09:28] <meejah> which way*
[2021-08-04 16:09:31] <exarkun> I think the thing we have to do is ... not accidentally remove the Foolscap protocol before we decide if we want to break the helper or bring it along?
[2021-08-04 16:09:55] <meejah> yeah i think it's perfectly fine to say "helper is Foolscap-only" (definitely "for now")
[2021-08-04 16:09:58] <exarkun> And not break the helper because of any changes we do meanwhile
[2021-08-04 16:10:18] <meejah> yeah, that latter is what i meant
[2021-08-04 16:10:20] <exarkun> Most of the helper implementation is in src/allmydata/immutable/offloaded.py right?
[2021-08-04 16:10:35] <meejah> i see some stuff in immutable/upload.py too
[2021-08-04 16:10:59] <meejah> (but, i've not delved deep in ever and am just grep'ing etc now too ;)
[2021-08-04 16:11:58] <meejah> yaeh looks like server-side is offloaded.py and (some/most?) of the client-side is in upload.py
[2021-08-04 16:13:23] <itamarst>  ok, so that's probably a decent stopping point
[2021-08-04 16:13:50] <itamarst> I will try to update notes with that, probably tomorrow
[2021-08-04 16:14:23] <itamarst> the next seems like ... implementing server? is security audit likely to majorly change anything?
[2021-08-04 16:16:27] <meejah> "hope not"
[2021-08-04 16:16:46] <exarkun> I'm not clear on whether I should be hoping for the auditors to have major findings or not
[2021-08-04 16:16:47] <meejah> maybe we should all take (another) critical pass at the spec?
[2021-08-04 16:17:41] <exarkun> itamarst: is it better to start with the hardest part of the project first - or the easiest part?
[2021-08-04 16:18:14] <itamarst> its' possible I can get the same outcome I want (better understanding of protocol) by reading code on client side
[2021-08-04 16:18:16] <exarkun> but yea actually I gotta go now
[2021-08-04 16:18:18] <itamarst> and the proposed spec
[2021-08-04 16:18:20] <meejah> what's the status of the haskell server? (i guess i really mean: could a PoC client talk to it successfull?)
[2021-08-04 16:18:21] <itamarst> so maybe I'll do that instead
[2021-08-04 16:18:56] <exarkun> meejah: nothing has ever talked to it, so who knows.  but I think immutables work and mutables... maybe half?
[2021-08-04 16:19:25] <itamarst> ... why would the storage server know about immutable vs. mutable?
[2021-08-04 16:19:41] <itamarst> or did it implement more than that
[2021-08-04 16:19:42] <meejah> okay .. i guess it's my bias, but i like "working code" so would be tempted to write PoC client refactoring (and try it vs. haskell server)
[2021-08-04 16:20:01] <exarkun> itamarst: question too large to answer today
[2021-08-04 16:20:11] <meejah> itamarst: yes, the mutable vs immutable interfaces are different
[2021-08-04 16:20:49] <meejah> (e.g. you have to prove you have the write-enabler to me allowed to mutate, approximately)
[2021-08-04 16:21:42] <itamarst> oh right I probalby read something about it that and forgot
[2021-08-04 16:21:47] <itamarst> anyway we can continue at a later day
[2021-08-04 16:22:16] <meejah> cool
[2021-08-04 16:22:46] <meejah> basically i think of them as separate things, since the code-paths are (usually) pretty different
```

## Question 4: How should support for two protocols be implemented in code structure, on the server side?

## Question 5: Can we create a (somewhat) formal protocol spec of the HTTP API?

There are various tools for specifying HTTP APIs.
This is useful for documentation, and also for validation.

For the HTTP level, options includes:

* OpenAPI (formerly Swagger).
  v3.0 apparently supports a bunch of JSONSchema, v3.1 supports all of it, but 3.1 tool support seems lacking (it's new).

Apparently that's pretty common so let's just say use that.

For the CBOR/JSON records:

* CDDL is apparently the schema language of choice for CBOR; it also supports JSON.
  There is a Rust implementation which could be wrapped for Python, but support for CDDL is in general not very broad (but might suffice).
* JSON Schema tools _might_ work with CBOR, not sure how bytes are handled though...

If the goal is generating client code, [Servant](https://docs.servant.dev/en/stable/) can do that.

## Question 6: What about helper client?

A "tahoe client" usually does the share-building + uploading itself, locally, so the amount of uploaded data is greater than the plaintext.
With the helper, you contact the "helper node" and upload ciphertext; it does the share-building etc there.
Thus user only upload the number of bytes in the ciphertext, rather than the multiple shares.

This is out of scope for now, just needs to not break.
