"""Pure helpers of the Interface Designer worker (the parts that don't need Efinity)."""

from efinity_mcp import pt_helper


class FakePTException(Exception):
    def __init__(self, msg, src=None):
        super().__init__(msg)
        self.msg = msg
        self.src_excp = src


def test_exception_chain_is_flattened():
    exc = FakePTException("Fail to set property", FakePTException("CLKOUT0_FREQ is read-only"))
    assert pt_helper.excp_text(exc) == "Fail to set property: CLKOUT0_FREQ is read-only"


def test_value_normalisation():
    # the API lists "1.8 V LVCMOS" but stores "1.8_V_LVCMOS"
    assert pt_helper.norm("1.8 V LVCMOS") == pt_helper.norm("1.8_V_LVCMOS")
    assert pt_helper.same_value("0", "0.0")
    assert pt_helper.same_value("100", "100.0")
    assert not pt_helper.same_value("1.8 V LVCMOS", "3.3 V LVCMOS")


def test_gpio_modes_are_complete():
    assert pt_helper.BUS_MODES <= set(pt_helper.GPIO_CREATORS)
    assert all(fn.startswith("create_") and fn.endswith("_gpio") for fn in pt_helper.GPIO_CREATORS.values())


def test_all_actions_registered():
    assert set(pt_helper.ACTIONS) == {"summary", "block", "resources", "check", "calc_pll", "edit", "export_isf"}
