# Failures the architecture is designed to expose

These are generalized lessons from operating document libraries and multi-agent deployments. No private incident transcript, business file, identifier or infrastructure address is included. Implemented checks below refer to runnable methods in `tests/test_library.py`; planned controls are deliberately distinguished from completed tests.

| Failure | Implemented response / evidence | Remaining boundary |
| --- | --- | --- |
| A nonempty sidecar contains only a filename, placeholder or one paragraph | `test_partial_and_placeholder_extraction_never_publish`; actual two-page PDF and blank-page verifier | Text on every page still does not prove visual fidelity |
| A hardcoded file ID retrieves an archived revision | `test_archive_and_restore_keep_all_snapshots`; current pointer, historical access explicit | Cloud ID resolution planned |
| A new brief points to old full text | `test_all_four_artifacts_are_verified`, `test_brief_is_separate_bounded_and_points_to_fulltext` | Semantic grounding of model-written claims planned |
| New NAS file is still being copied or emits duplicate events | `test_scan_waits_for_stability_and_recovers_after_restart`, `test_file_change_resets_stability_window`, `test_duplicate_event_is_idempotent` | Post-capture upstream edits require another scan |
| Every periodic run repeats expensive extraction | `test_unchanged_successful_source_is_not_reparsed_by_scan` | Large-corpus runtime and throughput benchmarks planned |
| Source disconnected, deleted or not completely listed | `test_nas_unavailable_keeps_last_publication_but_blocks_fresh_claim`, `test_source_disappearance_does_not_delete_current`, `test_incomplete_scan_blocks_publication` | Missing single-file exceptions remain in scan output; no automatic archival decision |
| Two writers replace current using the same old view | `test_two_threads_only_one_publication_wins`, `test_concurrent_plans_cannot_overwrite_current` | Multi-host/cloud compare-and-swap planned |
| Source, policy or approval content changes after review | `test_source_change_invalidates_publication`, `test_policy_change_invalidates_approval`, `test_stored_plan_tampering_is_rejected` | Trusted local operator; not protection from runtime administrators |
| Process interrupted between version switch and audit | `test_failed_transaction_retains_previous_current` | Power-loss and device-loss backup drill required |
| A renamed file is silently merged with the wrong document | `test_reused_identity_for_other_path_requires_migration`, `test_same_bytes_in_two_contexts_are_distinct_documents` | Explicit rename/family migration planned |
| A schema error is reported as no search result | `test_database_schema_drift_is_explicit`, `test_cli_error_is_json_and_nonzero` | Remote connector schema checks planned |
| Tests invent keys different from real production responses | `examples/demo.py` uses actual CLI core APIs; parser verifier uses actual LiteParse | New adapters must test actual response contracts too |
| Exit code 0 or `ok:false` is treated as successful delivery | Scan emits `ok:false` and exit 2 on incomplete inventory; apply reads back persisted current | Notification `sent/skipped/failed` outcomes and remote publication readback not implemented |
| A stale cache overwrites newer cloud data | Export only to a new directory; `test_bundle_detects_modified_brief_and_manifest` | Cloud adapter must pin expected prior pointer and read back; never accept a local timestamp alone |
| Symlink/junction deployment writes into the source repository | `test_symbolic_link_cannot_escape_scope`, `test_source_scope_and_runtime_overlap_are_rejected` | Mount administrators and OS ACLs remain trusted |
| Leading-zero IDs, Chinese paths, Windows short names or shell quoting break | `test_real_lifecycle_preserves_source_and_fulltext`, `test_windows_short_path_resolves_to_same_document`, Unicode source paths in every test, parser subprocess argument arrays | Cross-host mount naming and credentials require deployment canaries |
| A giant map consumes the agent context budget | `test_large_catalogs_are_bounded` (2,000 entries), `test_long_document_briefs_progressively_reveal` (1,000 pages) | Semantic routing quality and single-page long texts need evaluation |
| Human notes are overwritten by generated text | `test_human_notes_are_untouched`; exports never replace directories | Human notes need a stable-ID linking convention during migration |
| Different agents load stale runtime or only part of a skill update | Published artifact manifest hashes all files | Full skill/runtime deployment manifest and exact user-route canaries planned |
| Every scan notification is noisy because a timestamp changed | Audit records scan attempts; no notifications sent by core | Future notifications should hash semantic changes, report actionable exceptions and verify delivery |

One source document with a full Markdown, brief and several section cards is **one document**, not several new source files. `status` and bundle verification report document count separately from versions and exported files. Inventory/migration tools must similarly exclude generated indexes, archives and sidecars from unique-original counts.

Configuration review does not prove deployment. Each production adapter needs a synthetic canary through the same invocation path the user and scheduled agent will actually use, followed by independent readback. Preserve local changes during migration; compare divergent source/runtime copies before choosing a direction.
