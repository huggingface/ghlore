"""Section 10's benchmark: the evaluation set, and the runner that scores it.

Server-side only -- it reads the index. Nothing here is imported by ``relore.cli``
(invariant 1), and nothing it writes is ever retrievable (invariant 3): the dataset is a
file, the same file ``POST /api/v1/label`` appends to.
"""

from relore.bench.dataset import SLICES, Dataset, Example, load, save

__all__ = ["Dataset", "Example", "SLICES", "load", "save"]
