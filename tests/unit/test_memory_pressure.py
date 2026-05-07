"""Tests for memory pressure score — parse_vm_stat() and compute_pressure().

These tests are written BEFORE extracting the pure functions from
_compute_memory_pressure(). They should fail initially (RED phase)
and pass once the pure functions are extracted (GREEN phase).

Covers:
- parse_vm_stat() against known vm_stat output
- compute_pressure() with known inputs
- Gate threshold logic
- Edge cases: scientific notation, missing values
"""

import unittest


SAMPLE_VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               12345.
Pages active:                             56789.
Pages inactive:                           10111.
Pages speculative:                         2345.
Pages throttled:                              0.
Pages wired down:                         34567.
Pages purgeable:                           1234.
"Translation faults":                 99999999.
Pages copy-on-write:                     123456.
Pages zero filled:                       789012.
Pages reactivated:                        34567.
Pages purged:                              1234.
File-backed pages:                        45678.
Anonymous pages:                          78901.
Pages stored in compressor:               23456.
Pages occupied by compressor:             12345.
Decompressions:                           56789.
Pageouts:                                    12.
Pageins:                                  34567.
"""

VM_STAT_EMPTY = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               12345.
"""

VM_STAT_MISSING_FREE = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages active:                             56789.
"""

VM_STAT_SCIENTIFIC = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                            1.23e+09.
Pages inactive:                          500000.
Pages active:                             56789.
"""


class TestParseVmStat(unittest.TestCase):
    """Tests for parse_vm_stat()."""

    def test_parse_full_output(self):
        """parse_vm_stat() extracts free, inactive, active, page_size from full output."""
        from evolution.prompts.evolve_prompts import parse_vm_stat

        result = parse_vm_stat(SAMPLE_VM_STAT)
        self.assertEqual(result["free_pages"], 12345)
        self.assertEqual(result["inactive_pages"], 10111)
        self.assertEqual(result["page_size"], 16384)

    def test_parse_minimal_output(self):
        """parse_vm_stat() works with only free pages present."""
        from evolution.prompts.evolve_prompts import parse_vm_stat

        result = parse_vm_stat(VM_STAT_EMPTY)
        self.assertEqual(result["free_pages"], 12345)
        # inactive should default to 0 if not present
        self.assertEqual(result["inactive_pages"], 0)

    def test_parse_scientific_notation(self):
        """parse_vm_stat() handles scientific notation (e.g. '1.23e+09')."""
        from evolution.prompts.evolve_prompts import parse_vm_stat

        result = parse_vm_stat(VM_STAT_SCIENTIFIC)
        self.assertEqual(result["free_pages"], 1230000000)
        self.assertEqual(result["inactive_pages"], 500000)

    def test_parse_missing_free_raises_value_error(self):
        """parse_vm_stat() raises ValueError when Pages free is missing."""
        from evolution.prompts.evolve_prompts import parse_vm_stat

        with self.assertRaises(ValueError):
            parse_vm_stat(VM_STAT_MISSING_FREE)


class TestComputePressure(unittest.TestCase):
    """Tests for compute_pressure()."""

    def test_high_pressure(self):
        """Pressure = 0.902 when (500+300)/8192 of memory used."""
        from evolution.prompts.evolve_prompts import compute_pressure

        # 500 MB free, 300 MB inactive out of 8192 MB total
        pressure = compute_pressure(free=500, inactive=300, total=8192)
        self.assertAlmostEqual(pressure, 0.902, places=3)

    def test_low_pressure(self):
        """Pressure = 0.268 when (4000+2000)/8192 of memory used."""
        from evolution.prompts.evolve_prompts import compute_pressure

        pressure = compute_pressure(free=4000, inactive=2000, total=8192)
        self.assertAlmostEqual(pressure, 0.268, places=3)

    def test_zero_pressure(self):
        """Pressure = 0.0 when all memory is free."""
        from evolution.prompts.evolve_prompts import compute_pressure

        pressure = compute_pressure(free=8192, inactive=0, total=8192)
        self.assertEqual(pressure, 0.0)

    def test_full_pressure(self):
        """Pressure = 1.0 when no memory is free."""
        from evolution.prompts.evolve_prompts import compute_pressure

        pressure = compute_pressure(free=0, inactive=0, total=8192)
        self.assertEqual(pressure, 1.0)

    def test_clamps_above_zero(self):
        """Negative pressure values are clamped to 0.0."""
        from evolution.prompts.evolve_prompts import compute_pressure

        pressure = compute_pressure(free=8192, inactive=1000, total=8192)
        self.assertEqual(pressure, 0.0)

    def test_clamps_below_one(self):
        """Pressure values above 1.0 are clamped to 1.0."""
        from evolution.prompts.evolve_prompts import compute_pressure

        pressure = compute_pressure(free=-1, inactive=0, total=8192)
        self.assertEqual(pressure, 1.0)


class TestThresholdGate(unittest.TestCase):
    """Tests for threshold gating logic at 0.85."""

    THRESHOLD = 0.85

    def test_below_threshold_allows(self):
        """Pressure at 0.84 is below 0.85 threshold — should allow."""
        from evolution.prompts.evolve_prompts import compute_pressure

        pressure = compute_pressure(free=5000, inactive=1500, total=8192)
        self.assertLess(pressure, self.THRESHOLD)

    def test_above_threshold_suspends(self):
        """Pressure at ~0.854 is above 0.85 threshold — should suspend."""
        from evolution.prompts.evolve_prompts import compute_pressure

        pressure = compute_pressure(free=500, inactive=100, total=4096)
        # (500+100)/4096 = 0.1465, pressure = 1-0.1465 = 0.8535
        self.assertGreater(pressure, self.THRESHOLD)

    def test_at_threshold_boundary(self):
        """Pressure exactly at 0.85 should be allowed (<= threshold)."""
        from evolution.prompts.evolve_prompts import compute_pressure

        pressure = compute_pressure(free=1229, inactive=0, total=8192)
        # 1.0 - 1229/8192 = 1.0 - 0.150 = 0.85
        self.assertAlmostEqual(pressure, 0.85, places=2)


if __name__ == "__main__":
    unittest.main()
