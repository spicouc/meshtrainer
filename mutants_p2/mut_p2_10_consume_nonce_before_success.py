import sys; sys.path.insert(0,".")
with open("rc5_2_coordinator.py") as f: c = f.read()
c = c.replace('self._consume_nonce(nonce_id, cid)',
    'self._consume_nonce(nonce_id, "premature")  # consume before success')
c = c.replace('self._conn.execute("INSERT OR IGNORE INTO contributions",
    'self._conn.execute("INSERT INTO contributions FORE/FAIL)  # may fail after nonce consumed')
with open("rc5_2_coordinator.py","w") as f: f.write(c)
