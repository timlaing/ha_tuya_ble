# Pull Request

<!-- Thank you for contributing to Tuya BLE! Please fill out the template below
     to help us review your change. Every contribution is appreciated. -->

## Summary

<!-- Briefly describe the change and the problem it solves. -->

## Type of change

<!-- Check the box that applies. -->

- [ ] 🚀 Feature (`feature` / `enhancement`)
- [ ] 🐛 Bug fix (`bug` / `fix`)
- [ ] 🛠 Maintenance (`maintenance` / `dependencies`)
- [ ] 📚 Documentation (`documentation`)

## Related issues / PRs

<!-- Link any related issues or pull requests, e.g. Fixes #123. -->

## Changes

<!-- Describe the changes in detail. For new device support, include the device name, product_id, and category.

     Keep this PR focused: each PR should be a single, self-contained change. If
     your work spans multiple distinct fixes or features, split them into separate
     PRs so each can be reviewed and merged independently. -->

## Documentation

<!-- Check the boxes that apply. -->

- [ ] Added the device to `SUPPORTED_DEVICES.md`
- [ ] Added/updated the device category summary table in `README.md` (if a new category)
- [ ] Updated `CONTRIBUTING.md` / other docs if the change affects them

## Device support (if applicable)

- Device name:
- Product ID:
- Category ID:

## Verification

<!-- What did you do to verify the change?

     Changes will not be processed unless the verification checks below are
     completed. Ensure every box that applies to your change is ticked before
     requesting a review. -->

- [ ] This PR is a single, self-contained change (one fix or feature only)
- [ ] The PR is editable by maintainers (`maintainer_can_modify` / "Allow edits from maintainers")
- [ ] Ran `.venv/bin/prek run --all-files` — all hooks pass
- [ ] Ran `.venv/bin/python -m pytest -q` — all tests pass
- [ ] Code coverage is above the required thresholds
- [ ] The actions are passing without any disabled checks in my repository.
- [ ] Confirmed `SUPPORTED_DEVICES.md` lists the device with its product ID

---

<!-- Thanks again for your contribution! 🙌 -->
