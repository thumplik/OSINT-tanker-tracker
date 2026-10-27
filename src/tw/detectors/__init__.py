"""Detectors D1-D5, guards and composite scoring (SPEC §8).

Everything in this package is PURE: `(window_data, baseline, config) -> list[Alert]`.
No network, no randomness, no LLM, no ML. Thresholds come only from `config/detectors.yaml`.
"""
