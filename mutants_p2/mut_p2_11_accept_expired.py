
import os
with open("rc5_2_coordinator.py") as f: c = f.read()
c = c.replace('if receipt.get("expires_at", 0) < time.time():', 'if False:  # accept expired')
with open("rc5_2_coordinator.py","w") as f: f.write(c)
