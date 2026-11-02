import machine
import time
import ustruct

class MAX31855:
    def __init__(self, spi, cs_pin):
        self.spi = spi
        self.cs  = machine.Pin(cs_pin, machine.Pin.OUT)
        self.cs.value(1)
        self._buf = bytearray(4)

    def _read(self):
        self.cs.value(0)
        time.sleep_us(10)
        self.spi.readinto(self._buf)
        self.cs.value(1)
        return self._buf

    def _fault(self, data):
        if not (data[1] & 0x01):
            return None
        faults = []
        if data[3] & 0x04: faults.append("short to VCC")
        if data[3] & 0x02: faults.append("short to GND")
        if data[3] & 0x01: faults.append("open circuit")
        return ", ".join(faults) if faults else "unknown fault"

    def read(self):
        """Returns (tc_celsius, cj_celsius) or (None, None) on fault."""
        data = self._read()
        if self._fault(data):
            return None, None
        tc = (ustruct.unpack('>h', data[0:2])[0] >> 2) * 0.25
        cj = (ustruct.unpack('>h', data[2:4])[0] >> 4) * 0.0625
        return tc, cj

    def read_celsius(self):
        return self.read()[0]

    def fault_detail(self):
        return self._fault(self._read())