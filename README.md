asyncvarlink
============

This is a pure Python implementation of the [varlink](https://varlink.org) IPC
protocol based on `asyncio`. The main differences to he [reference
implementation](https://github.com/varlink/python) are:

 * Usage of `asyncio` instead of synchronous threading
 * Where the reference implementation parses a varlink interface description
   as a source of truth, this implementation derives a varlink interface
   description from a typed Python class to describe an interface.
 * Even though the [varlink faq](https://varlink.org/FAQ) explicitly renders
   passing file descriptors out of scope, `systemd` uses it and it is an
   important feature also implemented here.

Collaboration
=============

The primary means of collaborating on this project is
[github](https://github.com/helmutg/asyncvarlink). If you prefer not to use a
centralized forge, sending inquiries and patches to
[Helmut](mailto:helmut@subdivi.de?Subject=asyncvarlink) is also welcome.

License
=======

GPL-3
