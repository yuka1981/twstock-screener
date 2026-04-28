from twstock_screener.detectors.ascending_flag import AscendingFlagDetector
from twstock_screener.detectors.ascending_wedge import AscendingWedgeDetector
from twstock_screener.detectors.base import Detector, DetectorResult
from twstock_screener.detectors.descending_flag import DescendingFlagDetector
from twstock_screener.detectors.diamond_top import DiamondTopDetector
from twstock_screener.detectors.m_top import MTopDetector
from twstock_screener.detectors.rectangle import RectangleDetector
from twstock_screener.detectors.w_bottom import WBottomDetector

ALL_DETECTORS: list[Detector] = [
    MTopDetector(),
    DescendingFlagDetector(),
    DiamondTopDetector(),
    RectangleDetector(),
    WBottomDetector(),
    AscendingFlagDetector(),
    AscendingWedgeDetector(),
]

SELL_PATTERNS = {"m_top", "descending_flag", "diamond_top"}
BUY_PATTERNS = {"w_bottom", "ascending_flag", "ascending_wedge"}
BOX_PATTERNS = {"rectangle"}

__all__ = [
    "ALL_DETECTORS",
    "BOX_PATTERNS",
    "BUY_PATTERNS",
    "Detector",
    "DetectorResult",
    "SELL_PATTERNS",
]
