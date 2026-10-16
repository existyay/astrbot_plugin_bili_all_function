## 变更内容

<!-- 一句话说明这个 PR 做了什么 -->

## 动机 / 关联 Issue

<!-- 为什么要改；修复了哪个 Issue（Fixes #123） -->

## 变更类型

- [ ] Bug 修复（不改变现有行为契约）
- [ ] 新功能（新增指令 / 配置项 / 接口）
- [ ] 重构 / 性能优化
- [ ] 文档、测试或工程化改动

## 自检清单

- [ ] `python scripts/check_release.py` 全部通过（ruff / mypy / 合规 / 元数据 / 测试 / 徽章）
- [ ] 新增或修改的逻辑都有对应测试，整体覆盖率不低于 95%
- [ ] 测试全程离线（HTTP 通过 `tests/conftest.py` 的替身注入）
- [ ] 如涉及指令、配置项或对外行为，已同步更新 `README.md` 与 `CHANGELOG.md`
- [ ] 未提交任何真实 Cookie、Token 或本地绝对路径
- [ ] 如修改了 `metadata.yaml` / `_conf_schema.json` / `views/` / i18n，已确认 `tests/test_compliance.py` 通过

## 验证方式

<!-- 贴出执行的命令与关键结果 -->

```text
$ python scripts/check_release.py
...

```

## 对使用者配置或订阅数据的影响

<!-- 是否影响已有配置项、订阅数据文件（__schema__ 迁移）、推送行为 -->
