import unittest

from asyncvarlink.serviceinterface import (
    InterfaceNotFound,
    VarlinkServiceInterface,
)


class TestServiceInterface(unittest.TestCase):
    def setUp(self) -> None:
        self.vsi = VarlinkServiceInterface(
            "examplevendor", "exampleproduct", "1.0", "https://example.com"
        )
        self.vsi.register(self.vsi)

    def test_getinfo(self) -> None:
        self.assertEqual(self.vsi.name, "org.varlink.service")
        self.assertEqual(
            {
                "vendor": "examplevendor",
                "product": "exampleproduct",
                "version": "1.0",
                "url": "https://example.com",
                "interfaces": [self.vsi.name],
            },
            self.vsi.GetInfo(),
        )

    def test_description(self) -> None:
        self.assertEqual(
            {
                "description": """interface org.varlink.service

method GetInfo() -> (interfaces: []string, product: string, url: string, vendor: string, version: string)
method GetInterfaceDescription(interface: string) -> (description: string)
""",
            },
            self.vsi.GetInterfaceDescription(interface=self.vsi.name),
        )

    def test_missing_interface(self) -> None:
        self.assertRaises(
            InterfaceNotFound,
            self.vsi.GetInterfaceDescription,
            interface="com.example.nonexistent",
        )
