# RC5.4 Risk Register (Stage A)

- Mid-backward crash: graph not persistable -> unit EXPIRED (accepted limitation).
- Lease clock skew: coordinator + worker use wall clock; bounded by TTL margins.
- Double recovery: prevented by journal status transitions (APPLIED -> SUBMITTED -> COMMITTED).
- Stale lease renewal: rejected by nonce + session ownership.
