# 快速核验清单

发布前请至少确认：

- [ ] `core check-app apps.<app>.module --json`
- [ ] `core list-apps --installed-app apps.<app>.module --json`
- [ ] `core check-config --profile local --json`
- [ ] `smoke --profile local --json`

通过后可提交到 GitHub Pages 文档站点。
