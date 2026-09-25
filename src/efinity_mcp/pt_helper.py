"""Interface Designer worker. Runs under Efinity's bundled Python, not the server's.

    python pt_helper.py <request.json> <response.json>

The request names a .peri.xml and an action; the response is JSON. Everything goes through
Efinity's Interface Designer Python API (pt/bin/api_service/design.py). That API accepts
unknown property names, invalid values and nonexistent pins without complaint, so this
script validates each change against the API's own option lists and reads it back.

Only the Python standard library and Efinity's own modules may be imported here.
"""

from __future__ import annotations

import contextlib
import difflib
import io
import json
import os
import re
import sys
import traceback

if os.environ.get("EFXPT_HOME"):  # absent only when unit tests import the pure helpers
    sys.path.append(os.path.join(os.environ["EFXPT_HOME"], "bin"))

GPIO_CREATORS = {
    "input": "create_input_gpio",
    "output": "create_output_gpio",
    "inout": "create_inout_gpio",
    "open_drain_output": "create_open_drain_output_gpio",
    "clock_input": "create_input_clock_gpio",
    "regional_clock_input": "create_regional_input_clock_gpio",
    "pll_clock_input": "create_pll_input_clock_gpio",
    "pll_ext_feedback": "create_pll_ext_fb_gpio",
    "mipi_clock_input": "create_mipi_input_clock_gpio",
    "pcie_perstn": "create_pcie_perstn_gpio",
    "clockout": "create_clockout_gpio",
    "global_control": "create_global_control_gpio",
    "vref": "create_vref_gpio",
    "unused": "create_unused_gpio",
}
BUS_MODES = {"input", "output", "inout"}
SUMMARY_KEYS = ("MODE", "PRESET", "FREQ", "REFCLK_SOURCE", "REFCLK_FREQ", "REFCLK", "FEEDBACK_MODE", "CONN_TYPE")


class OpError(Exception):
    pass


def excp_text(exc: BaseException) -> str:
    """PTException chains hide the real reason in src_excp; flatten them."""
    parts = []
    while exc is not None:
        msg = getattr(exc, "msg", None) or str(exc) or type(exc).__name__
        if msg not in parts:
            parts.append(msg)
        exc = getattr(exc, "src_excp", None)
    return ": ".join(parts)


def norm(value) -> str:
    return re.sub(r"[\s_]+", "_", str(value).strip()).lower()


def same_value(a, b) -> bool:
    if norm(a) == norm(b):
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return False


class Peri:
    def __init__(self, req: dict):
        from api_service.common.infra import InfraService
        from api_service.common.object_db import APIObject
        from api_service.design import DesignAPI

        self.APIObject = APIObject
        self.path = req["peri"]
        self.d = DesignAPI(is_verbose=False)
        self.created = False
        if os.path.isfile(self.path):
            self.d.load(self.path)
        elif req.get("create_if_missing"):
            self.d.create(req["project_name"], req["device"], os.path.dirname(self.path))
            self.created = True
        else:
            raise OpError(f"Interface design {self.path} not found")
        self.infra = InfraService.get_instance()
        self.dev = self.d.get_device_api()
        self._gpio_res = None

    # ------------------------------------------------------------------ lookup

    def block_types(self) -> list[str]:
        return list(self.d.get_block_type())

    def gpio_resources(self, refresh=False) -> dict:
        if self._gpio_res is None or refresh:
            self._gpio_res = self.dev.get_gpio_resource() or {}
        return self._gpio_res

    def resolve(self, name: str, btype: str | None = None) -> tuple[str, int]:
        """(block type, object id) for an instance; GPIO buses report type GPIO_BUS."""
        btype = btype.upper() if btype else None
        if btype in (None, "GPIO", "GPIO_BUS"):
            oid = self.d.get_gpio(name)
            if oid is not None:
                return "GPIO", oid
            oid = self.d.get_bus(name)
            if oid is not None:
                return "GPIO_BUS", oid
            if btype:
                raise OpError(f"No GPIO named '{name}'")
        types = [btype] if btype else [t for t in self.block_types() if t != "GPIO"]
        for t in types:
            if t not in self.block_types():
                raise OpError(f"Block type {t} is not supported on this device. Types: {self.block_types()}")
            if name in (self.d.get_all_block_name(t) or []):
                oid = self.d.get_block(name, t)
                if oid is not None:
                    return t, oid
        raise OpError(f"No instance named '{name}'" + (f" of type {btype}" if btype else "") + ". " + self.name_hint(name))

    def all_names(self) -> list[str]:
        names = list(self.d.get_all_gpio_name())
        for t in self.block_types():
            if t != "GPIO":
                names += self.d.get_all_block_name(t) or []
        return names

    def name_hint(self, name: str) -> str:
        close = difflib.get_close_matches(name, self.all_names(), n=5, cutoff=0.5)
        return f"Did you mean: {close}" if close else "Use get_interface_design to list instances."

    def prop_target(self, btype: str, oid: int) -> tuple[str, int]:
        """Buses don't answer property queries; their first member does."""
        if btype == "GPIO_BUS":
            members = self.d.get_bus_gpio(oid)
            if not members:
                raise OpError("GPIO bus has no members")
            return "GPIO", members[0]
        return btype, oid

    def props(self, btype: str, oid: int) -> dict:
        ptype, poid = self.prop_target(btype, oid)
        raw = self.d.get_all_property(ptype, poid) or {}
        return {k: (v.get(k) if isinstance(v, dict) else v) for k, v in raw.items()}

    def options(self, btype: str, oid: int, prop: str) -> list[str] | None:
        """Allowed values: a list of choices, ['min:max'] for a range, [] / None when free-form."""
        ptype, poid = self.prop_target(btype, oid)
        svc = self.infra.get_prop_service(self.APIObject.str2otype_map.get(ptype), poid)
        if svc is None:
            return None
        try:
            opts = svc.get_valid_option(prop)
        except Exception:
            return None
        if opts is None or isinstance(opts, str):
            return [opts] if opts else None
        try:
            return sorted(str(o) for o in opts)
        except TypeError:
            return None

    # ------------------------------------------------------------------ reading

    def gpio_row(self, name: str) -> dict:
        oid = self.d.get_gpio(name)
        p = self.props("GPIO", oid)
        res = p.get("RESOURCE") or ""
        info = self.gpio_resources().get(res, {}) if res else {}
        row = {
            "name": name,
            "mode": p.get("MODE"),
            "io_standard": p.get("IO_STANDARD"),
            "resource": res or None,
            "pin": info.get("PACKAGE_PIN"),
            "bank": info.get("IO_BANK"),
        }
        for key in ("CONN_TYPE", "PULL_OPTION", "DRIVE_STRENGTH", "IN_REG", "OUT_REG"):
            val = p.get(key)
            if val not in (None, "", "NONE", "BYPASS"):
                row[key.lower()] = val
        m = re.match(r"^(.*)\[(\d+)\]$", name)
        if m and self.d.get_bus(m.group(1)) is not None:
            row["bus"] = m.group(1)
        return row

    def block_row(self, btype: str, name: str) -> dict:
        oid = self.d.get_block(name, btype)
        p = self.props(btype, oid)
        row = {"name": name, "type": btype, "resource": p.get("RESOURCE") or None}
        for key in SUMMARY_KEYS:
            if p.get(key) not in (None, ""):
                row[key.lower()] = p[key]
        clocks = []
        for n in range(8):
            if p.get(f"CLKOUT{n}_EN") == "1":
                clocks.append({"clkout": n, "pin": p.get(f"CLKOUT{n}_PIN"), "freq_mhz": p.get(f"CLKOUT{n}_FREQ")})
        if clocks:
            row["output_clocks"] = clocks
        return row

    def summary(self, req: dict) -> dict:
        want = {t.upper() for t in req.get("block_types") or []}
        out = {"file": self.path, "block_types_supported": self.block_types()}
        if not want or "GPIO" in want:
            out["gpio"] = [self.gpio_row(n) for n in self.d.get_all_gpio_name()]
            buses = sorted({g["bus"] for g in out["gpio"] if "bus" in g})
            if buses:
                out["gpio_buses"] = buses
        blocks = []
        for t in self.block_types():
            if t == "GPIO" or (want and t not in want):
                continue
            for name in self.d.get_all_block_name(t) or []:
                try:
                    blocks.append(self.block_row(t, name))
                except Exception as exc:
                    blocks.append({"name": name, "type": t, "error": excp_text(exc)})
        out["blocks"] = blocks
        if not want:
            out["bank_voltages"] = self.d.get_iobank_voltage()
            try:
                out["unused_gpio_state"] = self.d.get_gpio_unused_state()
            except Exception:
                pass
        if req.get("include_check"):
            out["design_check"] = self.check()
        return out

    def block_detail(self, req: dict) -> dict:
        btype, oid = self.resolve(req["name"], req.get("block_type"))
        props = self.props(btype, oid)
        out = {"name": req["name"], "type": btype}
        if btype == "GPIO_BUS":
            out["members"] = [self.props("GPIO", g).get("NAME") for g in self.d.get_bus_gpio(oid)]
            out["note"] = "Properties shown are the first member's; setting them on the bus name sets every member."
        out["properties"] = {k: v for k, v in props.items() if v is not None}
        inactive = sorted(k for k, v in props.items() if v is None)
        if inactive:
            out["inactive_properties"] = inactive
        options = {}
        for k in props:
            opts = self.options(btype, oid, k)
            if opts:
                options[k] = opts
        out["allowed_values"] = options
        out["allowed_values_note"] = "'a:b' means a numeric range; properties not listed take free-form values (pin names, frequencies)."
        if btype == "GPIO":
            row = self.gpio_row(req["name"])
            out["pin"], out["bank"] = row["pin"], row["bank"]
        if btype == "PLL":
            try:
                out["calculated"] = self.d.calc_pll_clock(oid)
            except Exception as exc:
                out["calculated_error"] = excp_text(exc)
            try:
                out["reference_clock_source"] = self.d.trace_ref_clock(oid)
            except Exception:
                pass
        if btype in ("DDR", "PMA_DIRECT"):
            try:
                out["preset"] = self.d.get_preset(req["name"], btype)
            except Exception:
                pass
        return out

    def resources(self, req: dict) -> dict:
        btype = (req.get("block_type") or "GPIO").upper()
        free_only = req.get("free_only", True)
        rows = []
        if btype == "GPIO":
            bank = (req.get("bank") or "").lower()
            feature = (req.get("feature") or "").lower()
            for res, info in sorted(self.gpio_resources().items()):
                used_by = info.get("INSTANCE") or None
                if free_only and used_by:
                    continue
                if bank and (info.get("IO_BANK") or "").lower() != bank:
                    continue
                if feature and feature not in (info.get("FEATURES") or "").lower() and feature not in (info.get("ALT_CONN") or "").lower():
                    continue
                rows.append({
                    "resource": res, "pin": info.get("PACKAGE_PIN"), "bank": info.get("IO_BANK"),
                    "features": info.get("FEATURES"), "alt_conn": None if info.get("ALT_CONN") in (None, "None") else info.get("ALT_CONN"),
                    "clock_region": info.get("CLK_REGION"), "used_by": used_by,
                })
        else:
            if btype not in self.block_types():
                raise OpError(f"Block type {btype} is not supported on this device. Types: {self.block_types()}")
            for res in self.dev.get_block_resource_name(btype) or []:
                used = bool(self.d.is_resource_used(res, btype))
                if free_only and used:
                    continue
                row = {"resource": res, "used": used}
                try:
                    feats = self.dev.get_block_resource_features(res, btype)
                    if feats:
                        row["features"] = feats
                except Exception:
                    pass
                rows.append(row)
        limit = int(req.get("limit") or 200)
        return {"block_type": btype, "total": len(rows), "shown": min(limit, len(rows)), "resources": rows[:limit]}

    def check(self) -> dict:
        ok = self.d.check_design()
        issues = []
        for i in self.d.get_design_check_issue(raw=True):
            sev = getattr(i.severity, "name", str(i.severity))
            issues.append({"severity": sev, "instance": i.instance_name, "type": i.instance_type, "rule": i.rule_name, "message": i.msg})
        order = {"error": 0, "warning": 1, "info": 2}
        issues.sort(key=lambda x: order.get(x["severity"], 3))
        return {
            "passed": bool(ok),
            "errors": sum(1 for x in issues if x["severity"] == "error"),
            "warnings": sum(1 for x in issues if x["severity"] == "warning"),
            "issues": issues,
        }

    def calc_pll(self, req: dict) -> dict:
        btype, oid = self.resolve(req["name"], "PLL")
        targets = {k.upper(): str(v) for k, v in (req.get("targets") or {}).items()}
        if not targets:
            return {"current": self.d.calc_pll_clock(oid)}
        bad = [k for k in targets if not re.match(r"^CLKOUT\d_(FREQ|PHASE)$", k)]
        if bad:
            raise OpError(f"targets keys must look like CLKOUT0_FREQ / CLKOUT0_PHASE, got {bad}")
        results = self.d.auto_calc_pll_clock(oid, targets, apply_optimal=False, result_len=int(req.get("max_results") or 5))
        if not results:
            raise OpError("The PLL calculator found no solution for these targets. Enable the output clocks "
                          "(CLKOUTn_EN=1), check REFCLK_FREQ and the feedback mode, or relax the targets.")
        return {"targets": targets, "solutions": results}

    # ------------------------------------------------------------------ editing

    def set_one(self, name: str, btype: str, oid: int, prop: str, value) -> dict:
        prop = prop.upper()
        props = self.props(btype, oid)
        if prop in ("RESOURCE",):
            raise OpError("Use an assign_resource (or assign_pin) operation to place an instance")
        if prop == "NAME":
            raise OpError("Renaming isn't supported; delete and recreate the instance")
        if prop not in props:
            close = difflib.get_close_matches(prop, list(props), n=6, cutoff=0.4)
            raise OpError(f"{name} ({btype}) has no property {prop}." + (f" Close matches: {close}" if close else ""))
        value = str(value)
        opts = self.options(btype, oid, prop) or []
        if len(opts) == 1 and re.match(r"^-?(0x)?[\da-f.]+:-?(0x)?[\da-f.]+$", opts[0], re.I):
            lo, hi = opts[0].split(":")
            try:
                base = 16 if "0x" in opts[0].lower() else 10
                num = int(value, 0) if base == 16 else float(value)
                lo_n = int(lo, 16) if base == 16 else float(lo)
                hi_n = int(hi, 16) if base == 16 else float(hi)
            except ValueError:
                raise OpError(f"{prop} must be a number in {opts[0]}, got '{value}'") from None
            if not lo_n <= num <= hi_n:
                raise OpError(f"{prop}={value} is outside the allowed range {opts[0]}")
        elif opts:
            match = next((o for o in opts if o == value), None) or next((o for o in opts if norm(o) == norm(value)), None)
            if match is None:
                raise OpError(f"'{value}' is not a valid {prop}. Allowed: {opts}")
            value = match
        before = props.get(prop)
        try:
            self.d.set_property(oid, prop, value, block_type="GPIO" if btype == "GPIO_BUS" else btype)
        except Exception as exc:
            raise OpError(f"Setting {prop}={value} failed: {excp_text(exc)}") from None
        after = self.props(btype, oid).get(prop)
        change = {"property": prop, "from": before, "to": after}
        if not same_value(after, value):
            change["warning"] = f"Requested {value!r} but the design now reports {after!r}"
        return change

    def set_props(self, name: str, btype: str, oid: int, properties: dict) -> list[dict]:
        # MODE / type switches change which other properties exist, so apply them first.
        first = ("MODE", "CONN_TYPE", "REFCLK_SOURCE", "FEEDBACK_MODE")
        keys = sorted(properties, key=lambda k: (k.upper() not in first, first.index(k.upper()) if k.upper() in first else 0))
        return [self.set_one(name, btype, oid, k, properties[k]) for k in keys]

    def place(self, name: str, pin: str | None = None, resource: str | None = None, btype: str | None = None, override=False) -> dict:
        btype, oid = self.resolve(name, btype)
        if btype == "GPIO_BUS":
            raise OpError(f"{name} is a bus; assign each member, e.g. '{name}[0]'")
        if btype == "GPIO":
            res_map = self.gpio_resources(refresh=True)
            if pin:
                hits = [(r, i) for r, i in res_map.items() if (i.get("PACKAGE_PIN") or "").upper() == pin.upper()]
                if not hits:
                    raise OpError(f"Package pin {pin} doesn't exist on this device or isn't a GPIO. "
                                  "Use list_device_resources to find pins.")
                resource = hits[0][0]
            if resource not in res_map:
                close = difflib.get_close_matches(resource or "", list(res_map), n=5)
                raise OpError(f"GPIO resource {resource} doesn't exist." + (f" Close: {close}" if close else ""))
            info = res_map[resource]
            used_by = info.get("INSTANCE")
            if used_by and used_by != name and not override:
                raise OpError(f"{resource} (pin {info.get('PACKAGE_PIN')}) is already used by {used_by}. "
                              "Pass override=true to take it anyway.")
            before = self.props("GPIO", oid).get("RESOURCE")
            try:
                self.d.assign_resource(oid, resource, "GPIO", override=True)
            except Exception as exc:
                raise OpError(f"Assigning {resource} failed: {excp_text(exc)}") from None
            info = self.gpio_resources(refresh=True).get(resource, {})
            return {"from": before or None, "to": resource, "pin": info.get("PACKAGE_PIN"), "bank": info.get("IO_BANK"),
                    **({"took_from": used_by} if used_by and used_by != name else {})}
        if pin:
            raise OpError(f"{name} is a {btype}; place it with 'resource' (see list_device_resources), not a pin")
        names = self.dev.get_block_resource_name(btype) or []
        if resource not in names:
            raise OpError(f"{btype} resource {resource} doesn't exist. Resources: {names[:40]}")
        if self.d.is_resource_used(resource, btype) and self.d.get_resource(oid, btype) != resource and not override:
            raise OpError(f"{resource} is already used. Pass override=true to take it anyway.")
        before = self.d.get_resource(oid, btype)
        try:
            self.d.assign_resource(oid, resource, btype, override=True)
        except Exception as exc:
            raise OpError(f"Assigning {resource} failed: {excp_text(exc)}") from None
        return {"from": before or None, "to": self.d.get_resource(oid, btype)}

    def op(self, o: dict) -> dict:
        kind = o.get("op")
        name = o.get("name")
        if kind == "create_gpio":
            mode = (o.get("mode") or "").lower()
            if mode not in GPIO_CREATORS:
                raise OpError(f"mode must be one of {sorted(GPIO_CREATORS)}")
            msb, lsb = o.get("msb"), o.get("lsb")
            fn = getattr(self.d, GPIO_CREATORS[mode])
            try:
                if msb is not None or lsb is not None:
                    if mode not in BUS_MODES:
                        raise OpError(f"Buses (msb/lsb) are only allowed for {sorted(BUS_MODES)}")
                    oid = fn(name, int(msb), int(lsb if lsb is not None else 0))
                else:
                    oid = fn(name)
            except OpError:
                raise
            except Exception as exc:
                raise OpError(f"Creating GPIO {name} failed: {excp_text(exc)}") from None
            if oid is None:
                raise OpError(f"Creating GPIO {name} failed")
            btype = "GPIO_BUS" if msb is not None else "GPIO"
            out = {"created": name, "type": btype, "mode": mode}
            if o.get("properties"):
                out["properties"] = self.set_props(name, btype, oid, o["properties"])
            if o.get("pin") or o.get("resource"):
                if btype == "GPIO_BUS":
                    raise OpError("Place bus members one at a time with assign_pin on 'name[i]'")
                out["placement"] = self.place(name, o.get("pin"), o.get("resource"), "GPIO", o.get("override", False))
            return out

        if kind == "create_block":
            btype = (o.get("type") or "").upper()
            if btype == "GPIO":
                raise OpError("Use create_gpio for GPIOs")
            if btype not in self.block_types():
                raise OpError(f"Block type {btype} is not supported on this device. Types: {self.block_types()}")
            try:
                oid = self.d.create_block(name, btype, **(o.get("params") or {}))
            except Exception as exc:
                raise OpError(f"Creating {btype} {name} failed: {excp_text(exc)}") from None
            if oid is None:
                raise OpError(f"Creating {btype} {name} failed")
            out = {"created": name, "type": btype}
            if o.get("resource"):
                out["placement"] = self.place(name, None, o["resource"], btype, o.get("override", False))
            if o.get("properties"):
                out["properties"] = self.set_props(name, btype, oid, o["properties"])
            return out

        if kind == "delete":
            btype, oid = self.resolve(name, o.get("type"))
            try:
                if btype in ("GPIO", "GPIO_BUS"):
                    self.d.delete_gpio(oid)
                else:
                    self.d.delete_block(oid, btype)
            except Exception as exc:
                raise OpError(f"Deleting {name} failed: {excp_text(exc)}") from None
            return {"deleted": name, "type": btype}

        if kind == "set_properties":
            props = o.get("properties") or {}
            if not props:
                raise OpError("set_properties needs a non-empty 'properties' object")
            btype, oid = self.resolve(name, o.get("type"))
            return {"name": name, "type": btype, "changes": self.set_props(name, btype, oid, props)}

        if kind == "assign_pin":
            if not o.get("pin"):
                raise OpError("assign_pin needs 'pin' (package ball, e.g. 'U4')")
            return {"name": name, **self.place(name, pin=o["pin"], override=o.get("override", False))}

        if kind == "assign_resource":
            if not o.get("resource"):
                raise OpError("assign_resource needs 'resource' (e.g. GPIOB_P_31, PLL_TL0)")
            return {"name": name, **self.place(name, resource=o["resource"], btype=o.get("type"), override=o.get("override", False))}

        if kind == "set_bank_voltage":
            bank, volt = o.get("bank"), str(o.get("voltage", "")).strip()
            banks = self.d.get_all_iobank_name()
            if bank not in banks:
                raise OpError(f"Unknown I/O bank {bank}. Banks: {banks}")
            if not re.match(r"^\d(\.\d+)?$", volt):
                raise OpError(f"voltage should look like '1.8' or '3.3', got '{volt}'")
            before = self.d.get_iobank_voltage(bank)
            try:
                self.d.set_iobank_voltage(bank, volt)
            except Exception as exc:
                raise OpError(f"Setting bank {bank} to {volt} V failed: {excp_text(exc)}") from None
            after = self.d.get_iobank_voltage(bank)
            if not same_value(after, volt):
                raise OpError(f"Bank {bank} did not accept {volt} V (still {after})")
            return {"bank": bank, "from": before, "to": after}

        if kind == "auto_calc_pll":
            btype, oid = self.resolve(name, "PLL")
            targets = {k.upper(): str(v) for k, v in (o.get("targets") or {}).items()}
            if not targets:
                raise OpError("auto_calc_pll needs 'targets', e.g. {'CLKOUT0_FREQ': 200}")
            try:
                results = self.d.auto_calc_pll_clock(oid, targets, apply_optimal=True, result_len=1)
            except Exception as exc:
                raise OpError(f"PLL calculation failed: {excp_text(exc)}") from None
            if not results:
                raise OpError("The PLL calculator found no solution. Enable the output clocks (CLKOUTn_EN=1) "
                              "first and check REFCLK_FREQ, or relax the targets.")
            return {"name": name, "applied": results[0], "calculated": self.d.calc_pll_clock(oid)}

        if kind == "gen_pll_ref_clock":
            btype, oid = self.resolve(name, "PLL")
            kwargs = {k: o[k] for k in ("refclk_name", "pll_res") if o.get(k)}
            try:
                self.d.gen_pll_ref_clock(oid, **kwargs)
            except Exception as exc:
                raise OpError(f"Generating the PLL reference clock failed: {excp_text(exc)}") from None
            return {"name": name, "reference_clock_source": self.d.trace_ref_clock(oid)}

        if kind == "set_unused_gpio_state":
            before = self.d.get_gpio_unused_state()
            self.d.set_gpio_unused_state(str(o.get("state")))
            return {"from": before, "to": self.d.get_gpio_unused_state()}

        if kind == "import_isf":
            path = o.get("file")
            if not path or not os.path.isfile(path):
                raise OpError(f"ISF file not found: {path}")
            try:
                ok, issues = self.d.import_design(path)
            except Exception as exc:
                raise OpError(f"Importing {path} failed: {excp_text(exc)}") from None
            rows = [str(i) for i in issues or []]
            if not ok:
                raise OpError(f"Importing {path} failed: {rows[:20]}")
            return {"imported": path, "issues": rows[:50]}

        raise OpError(f"Unknown op '{kind}'. Ops: create_gpio, create_block, delete, set_properties, assign_pin, "
                      "assign_resource, set_bank_voltage, auto_calc_pll, gen_pll_ref_clock, set_unused_gpio_state, import_isf")

    def edit(self, req: dict) -> dict:
        def error_keys(check):
            return {(i["instance"], i["rule"], i["message"]) for i in check["issues"] if i["severity"] == "error"}

        baseline = error_keys(self.check()) if not self.created else set()
        applied = []
        for idx, o in enumerate(req.get("operations") or []):
            try:
                applied.append({"op": o.get("op"), **self.op(o)})
            except OpError as exc:
                return {
                    "saved": False,
                    "failed_operation": {"index": idx, "operation": o, "error": str(exc)},
                    "applied_before_failure": applied,
                    "note": "Nothing was saved; the file is unchanged.",
                }
        check = self.check()
        new_errors = [i for i in check["issues"] if i["severity"] == "error"
                      and (i["instance"], i["rule"], i["message"]) not in baseline]
        fixed = len(baseline - error_keys(check))
        out = {"applied": applied, "design_check": {"passed": check["passed"], "errors": check["errors"],
                                                     "warnings": check["warnings"], "new_errors": new_errors,
                                                     "errors_fixed": fixed}}
        other = [i for i in check["issues"] if i["severity"] != "error" or i not in new_errors]
        if other:
            out["design_check"]["other_issues"] = other[:40]
        if req.get("dry_run"):
            out["saved"] = False
            out["note"] = "Dry run: nothing was saved."
            return out
        if new_errors and not req.get("allow_new_errors"):
            out["saved"] = False
            out["note"] = ("Not saved: these changes add design-check errors. Fix them in the same call, "
                           "or pass allow_new_errors=true to save anyway.")
            return out
        original = _SNAPSHOT.get(self.path)
        if original is not None:
            # back up the file as it was before this call (loading may already have rewritten it)
            backup = self.path + ".bak"
            _write_back(backup, original)
            out["backup"] = backup
        self.d.save_as(self.path, overwrite=True)
        out["saved"] = True
        out["file"] = self.path
        if self.created:
            out["created_design"] = True
            # create() saves an older db_version that Efinity upgrades (and rewrites) on the first
            # load; do that now, while writing is expected, so later reads find a current file.
            self.d.load(self.path)
        return out

    def export_isf(self, req: dict) -> dict:
        target = req["isf_file"]
        types = [t.upper() for t in req.get("block_types") or []] or None
        insts = req.get("instances") or []
        try:
            self.d.export_design(types, insts, target, bool(req.get("export_all_pins")))
        except Exception as exc:
            raise OpError(f"Export failed: {excp_text(exc)}") from None
        if not os.path.isfile(target):
            raise OpError("Export reported success but no file was written")
        with open(target, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        return {"isf_file": target, "lines": text.count("\n"), "preview": text[:4000]}


ACTIONS = {
    "summary": Peri.summary,
    "block": Peri.block_detail,
    "resources": Peri.resources,
    "check": lambda p, req: p.check(),
    "calc_pll": Peri.calc_pll,
    "edit": Peri.edit,
    "export_isf": Peri.export_isf,
}


# Loading a design written by an older Efinity (or freshly made by create()) makes the API upgrade
# and rewrite the file on disk. Only a saved edit may change the file, so every run snapshots it
# first and puts it back afterwards otherwise.
_SNAPSHOT: dict[str, tuple[bytes, float, float]] = {}


def _write_back(path: str, snap: tuple[bytes, float, float]):
    data, atime, mtime = snap
    with open(path, "wb") as fh:
        fh.write(data)
    os.utime(path, (atime, mtime))


def _snapshot(path: str):
    if os.path.isfile(path):
        st = os.stat(path)
        with open(path, "rb") as fh:
            _SNAPSHOT[path] = (fh.read(), st.st_atime, st.st_mtime)


def _restore_if_changed(path: str) -> bool:
    snap = _SNAPSHOT.get(path)
    if snap is None or not os.path.isfile(path):
        return False
    with open(path, "rb") as fh:
        if fh.read() == snap[0]:
            return False
    _write_back(path, snap)
    return True


def main():
    req_path, resp_path = sys.argv[1], sys.argv[2]
    with open(req_path, encoding="utf-8") as fh:
        req = json.load(fh)
    _snapshot(req["peri"])
    buf = io.StringIO()
    resp: dict
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            peri = Peri(req)
            resp = {"ok": True, "result": ACTIONS[req["action"]](peri, req)}
    except OpError as exc:
        resp = {"ok": False, "error": str(exc)}
    except Exception as exc:
        resp = {"ok": False, "error": excp_text(exc), "traceback": traceback.format_exc()[-3000:]}
    saved = resp.get("ok") and isinstance(resp.get("result"), dict) and resp["result"].get("saved") is True
    if not saved and _restore_if_changed(req["peri"]) and isinstance(resp.get("result"), dict):
        resp["result"]["file_format_note"] = (
            "Efinity upgraded this design's file format while loading it; the file on disk was left "
            "unchanged. It is upgraded on the next saved edit."
        )
    msgs = [line.strip() for line in buf.getvalue().splitlines()
            if re.match(r"^\s*(ERROR|CRITICAL|WARNING)\b", line) and "non-fatal warning messages" not in line]
    if msgs:
        resp["tool_messages"] = list(dict.fromkeys(msgs))[:40]
    with open(resp_path, "w", encoding="utf-8") as fh:
        json.dump(resp, fh, default=str)


if __name__ == "__main__":
    main()
