---
name: release
description: |
  发布 mnemosync 到 main。当用户要求发布、上线、部署到 main、
  推送 release 时使用此 skill。
---

# 发布 Skill

引导用户完成从 `dev` 到 `main` 的发布流程。

前提：版本号已在 `pyproject.toml` 中更新完毕（在调用本 skill 之前完成）。

## 发布步骤

### 1. 确认版本号

从 `pyproject.toml` 读取当前 `version`，向用户确认。

### 2. 冒烟测试

```bash
uv run pytest tests/ --no-cov -x -q
cd ui && npm install --legacy-peer-deps && npm run build && cd ..
```

两项都必须通过才能继续。

### 3. 创建发布分支并合并到 main

```bash
git checkout dev
git pull origin dev
git checkout -b release/v{VERSION}
git add -A
git commit -m "release: v{VERSION}"
git checkout main
git merge --no-ff release/v{VERSION} -m "release: v{VERSION}"
git push origin main
```

### 4. 打 tag 并推送

```bash
git tag -a v{VERSION} -m "Mnemosync v{VERSION}"
git push origin v{VERSION}
```

这会触发 GitHub Actions（`.github/workflows/release.yml`），自动构建 UI 并创建包含 `ui-dist.tar.gz` 的 GitHub Release。

### 5. 清理

```bash
git checkout dev
git branch -d release/v{VERSION}
```

### 6. 验证 GitHub Actions

使用 `gh` CLI 检查 release workflow 状态，等待最多 5 分钟：

```bash
sleep 10
gh run watch $(gh run list --workflow=release.yml --limit=1 --json databaseId --jq '.[0].databaseId')
```

**必须确认 `conclusion` 为 `success` 才算发布完成。**

如果 workflow 失败：
1. 查看失败日志：`gh run view <run-id> --log-failed`
2. 在 `dev` 分支修复问题，重新走发布流程
3. 删除失败的 tag，重新打 tag 推送

验证 Release 已创建：
```bash
gh release view v{VERSION} --json tagName,publishedAt,assets
```

## 回滚

```bash
git tag -d v{VERSION}
git push origin :refs/tags/v{VERSION}
gh release delete v{VERSION} --yes
```

## 注意事项

- 绝不要对 `main` 执行 force-push
- `install.sh` 会自动拉取最新 release，无需通知用户
