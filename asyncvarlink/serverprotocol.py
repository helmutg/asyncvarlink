# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-3

"""asyncio varlink server protocol implementation"""

import asyncio

from .error import VarlinkErrorReply, GenericVarlinkErrorReply
from .message import VarlinkMethodCall, VarlinkMethodReply
from .protocol import VarlinkProtocol
from .types import JSONObject, OwnedFileDescriptors


class VarlinkServerProtocol(VarlinkProtocol):
    """Protocol class for a varlink service. It receives calls as
    VarlinkMethodCall objects and issues replies as VarlinkMethodReply or
    VarlinkErrorReply objects. A derived class should implement call_received.
    """

    def send_reply(
        self,
        reply: VarlinkMethodReply | VarlinkErrorReply,
        fds: list[int] | None = None,
        autoclose: bool = True,
    ) -> asyncio.Future[None]:
        """Enqueue the given reply and file descriptors for sending. For the
        semantics regarding fds, please refer to the documentation of
        send_message.
        """
        return self.send_message(reply.tojson(), fds, autoclose)

    def request_received(
        self, obj: JSONObject, fds: OwnedFileDescriptors
    ) -> asyncio.Future[None] | None:
        try:
            try:
                call = VarlinkMethodCall.fromjson(obj)
            except (TypeError, ValueError):
                raise GenericVarlinkErrorReply("ProtocolViolation") from None
            return self.call_received(call, fds)
        except VarlinkErrorReply as err:
            if not obj.get("oneway", False):
                self.send_reply(err)
            return None

    def call_received(
        self, call: VarlinkMethodCall, fds: OwnedFileDescriptors
    ) -> asyncio.Future[None] | None:
        """Handle a received varlink parsed as a call object and associated
        file descriptors. The descriptors are valid until the function returns
        None or the returned future is complete. The function should call the
        send_reply method as needed or raise a VarlinkErrorReply to be sent by
        the caller.
        """
        raise NotImplementedError
