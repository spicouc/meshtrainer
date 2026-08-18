# RC5.3 Stage B R5 — Risk Register

| ID | Risc | Probabilitat | Impacte | Mitigació | Estat |
|---|---|---|---|---|---|
| RSK-01 | Server model reconstruït des de bytes alterats | Baixa | Alt | SHA-256 verificat al register i a step.open; bytes persistits per ronda | Mitigat |
| RSK-02 | Registre parcial davant error (dades inconsistents persistides) | Baixa | Alt | Transaccions BEGIN IMMEDIATE/COMMIT/ROLLBACK a totes les escriptures | Mitigat |
| RSK-03 | Assignment reemplaçable silenciosament (INSERT OR REPLACE) | Baixa | Alt | request_sha canònic; mateix ID + payload diferent = REJECTED | Mitigat |
| RSK-04 | Calibració reemplaçable / worker no calibrat assignat | Mitjana | Alt | Clau run+round+worker+nonce; sense defaults; uncalibrated rejected | Mitigat |
| RSK-05 | Lineage per ordre lexicogràfic de round_id | Baixa | Alt | round_index + parent_round_id explícit; r10/r2 provat | Mitigat |
| RSK-06 | Contribució fabricada (receipt/bundle arbitrari) | Baixa | Alt | register_uploaded_contribution(contribution_id) amb verificació completa + source_request_sha | Mitigat |
| RSK-07 | FedAvg amb SUPERSEDED o ETT ignorat | Baixa | Mitjà | Oracle independent; només ACTIVE; ETT > 0; weighted != simple provat | Mitigat |
| RSK-08 | Deltes idèntics entre shards (gradient no flueix) | Mitjana | Alt | delta real post−pre; lora_B no-zero; un sol optimizer.step | Mitigat |
| RSK-09 | Doble optimizer.step en replay | Mitjana | Alt | apply_update_once exactly-once; journal; ADV-R3-24 | Mitigat |
| RSK-10 | Restart amb activacions diferents | Baixa | Alt | Model reconstruït dels mateixos bytes persistits; P6/ADV-R3-22 | Mitigat |
| RSK-11 | Omissió d'un worker obligatori al quòrum | Baixa | Alt | ready_to_close exigeix exactament 1 ACTIVE per assignment | Mitigat |
| RSK-12 | /tmp ple durant gates (tmpfs 7.7G) | Mitjana | Alt | TMPDIR=/root/tmp_r53; backup de residus | Mitigat |
