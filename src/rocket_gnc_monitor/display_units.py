"""Display conversions from OpenRocket UnitGroup's default unit definitions.

SI data, recordings, protocols, configuration and controller calculations stay
unchanged. Source: core/.../unit/UnitGroup.java, setDefaultImperialUnits().
OpenRocket intentionally uses N, Ns and mbar in its Imperial preset.
"""
from dataclasses import dataclass

from .domain import finite


@dataclass(frozen=True)
class DisplayUnits:
    system: str = "imperial"

    def unit(self, quantity):
        metric = dict(distance="m", velocity="m/s", acceleration="m/s²", temperature="°C",
                      wind="m/s", mass="g", length="cm", pressure="mbar", force="N", impulse="Ns", rate="r/s")
        imperial = dict(distance="ft", velocity="ft/s", acceleration="ft/s²", temperature="°F",
                        wind="mph", mass="oz", length="in")
        return (imperial.get(quantity, metric[quantity]) if self.system == "imperial" else metric[quantity])

    def factor(self, quantity):
        if quantity == "rate":
            return 1 / 360  # Samples store degrees/second; OpenRocket defaults to revolutions/second.
        if quantity == "pressure":
            return .01  # Pa -> mbar in both OpenRocket presets.
        if self.system == "imperial":
            return dict(distance=1 / .3048, velocity=1 / .3048, acceleration=1 / .3048,
                        wind=1 / .44704, mass=1 / .0283495231, length=1 / .0254,
                        temperature=1.8).get(quantity, 1)
        return dict(mass=1000, length=100).get(quantity, 1)

    def convert(self, value, quantity):
        if not finite(value):
            return None
        return value * self.factor(quantity) + (32 if quantity == "temperature" and self.system == "imperial" else 0)

    def label(self, text):
        for old, quantity in (("m/s", "velocity"), ("m", "distance"), ("°C", "temperature")):
            text = text.replace(f"({old})", f"({self.unit(quantity)})")
        return text

    def text(self, value, quantity, decimals=1):
        converted = self.convert(value, quantity)
        return "—" if converted is None else f"{converted:,.{decimals}f} {self.unit(quantity)}"
