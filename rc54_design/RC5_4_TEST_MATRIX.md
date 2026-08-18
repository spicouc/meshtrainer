# RC5.4 Test Matrix (Stage A)

| Case | Expected |
|---|---|
| crash before open | clean retry |
| crash after open | lease expiry -> EXPIRED -> reassign |
| crash after commit | receipt replay identical |
| crash after upload | checkpoint retry idempotent |
| mid-backward | documented not-recoverable; EXPIRED; no partial contribution |
