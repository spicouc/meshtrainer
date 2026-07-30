import os
with open("rc5_2_coordinator.py") as f: c = f.read()
# Consume nonce before storing contribution
c = c.replace("self._consume_nonce(nonce_id, cid)", "self._conn.execute('INSERT OR IGNORE INTO nonces (nonce_id, consumed, cid) VALUES (?,1,?)', (nonce_id, 'PREMATURE')); self._conn.commit()")
# Don't store the contribution
c = c.replace("INSERT OR IGNORE INTO contributions", "INSERT OR FAIL INTO contributions_premature")
with open("rc5_2_coordinator.py","w") as f: f.write(c)
