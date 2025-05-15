asyncvarlink
============

`Varlink`_ is an inter-process communication protocol based on `JSON`_ and bidirectional streams between two processes.
There are multiple implementations for various programming languages including the `reference implementation for Python`_.
``asyncvarlink`` is an alternative implementation in pure Python using different design choices.
There are three main differences.

* As the name implies, it is based on ``asyncio``.
  If you prefer synchronous interaction, consider wrapping your interaction in ``asyncio.run`` or using the reference implementation.
* Interface definitions are modelled as typed Python classes and the actual varlink interface definition is rendered from those classes.
  In contrast, the reference implementation parses interface definitions.
  As a consequence, using an existing varlink interface definition requires translating it to a Python class.
  This may seem like an odd choice initially, but there often are multiple Python types suitable for representing a varlink type.
  The reference implementation is less flexible and has a fixed type choice.
* Even though the `varlink FAQ`_ explicitly renders passing file descriptors out of scope, `systemd`_ uses that feature.
  Therefore ``asyncvarlink`` implements the same file descriptor passing scheme whereas the reference implementation does not.


.. _Varlink: https://varlink.org
.. _JSON: https://www.json.org/
.. _reference implementation for Python: https://github.com/varlink/python
.. _varlink FAQ: https://varlink.org/FAQ
.. _systemd: https://systemd.io

Contents
--------

.. toctree::

   usage
   structure
