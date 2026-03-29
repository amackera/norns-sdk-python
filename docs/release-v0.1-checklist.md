# norns-sdk-python v0.1 Release Checklist

## 1) Packaging + versioning
- [ ] Confirm package name/version in `pyproject.toml`
- [ ] Add/refresh `CHANGELOG.md`
- [ ] Tag `v0.1.0`

## 2) Compatibility alignment (with Norns)
- [ ] Verify worker join payload contract against current Norns main
- [ ] Verify `NornsClient.send_message` response shape (`run_id`, `status`)
- [ ] Verify event streaming behavior on `agent:<id>` channel

## 3) Tests
- [ ] Unit tests pass (`pytest`)
- [ ] Add/verify integration test against local Norns stack
- [ ] Validate reconnect and queued task flush behavior

## 4) Docs
- [ ] README quickstart validated end-to-end
- [ ] `docs/messaging-client-design.md` up to date
- [ ] `docs/remaining-work.md` reflects reality

## 5) Publish
- [ ] Build wheel/sdist
- [ ] Publish to TestPyPI
- [ ] Smoke install from TestPyPI
- [ ] Publish to PyPI

## 6) Post-release
- [ ] Create GitHub release notes
- [ ] Add first-party example links (hello-agent, Mimir)
- [ ] Track first adopter issues
