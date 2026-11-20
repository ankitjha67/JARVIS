"""Jarvis — personal desktop intelligence."""

import os as _os

# The numeric libraries behind the wake word start one worker thread per CPU core by default, which
# reserved about 1 GB of memory on a 16-thread machine for a model that needs a single thread.
# Measured: 1210 MB → 245 MB private bytes with these limits. Must be set before numpy is imported.
for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    _os.environ.setdefault(_name, "1")

__version__ = "0.8.0"
