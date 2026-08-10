---
name: release
description: |
  发布 mnemosync。支持两种发布路径:
  - beta 预发布 (dev → beta): 合并 dev 到 beta 供预发布测试
  - main 正式发布 (beta → main): 合并 beta 到 main 并打 tag
  当用户要求发布、上线、部署、推送 release、发 beta 时使用此 skill。
---

# 发布 Skill

引导用户完成 Mnemosync 的发布流程。**禁止直接从 dev 发布到 main**——必须先经 beta 预发布测试。

## 分支拓扑

```
dev (开发主线)
 │  merge ──► beta (预发布, install.sh 默认 beta + preview UI)
                  │  merge ──► main (正式版, install.sh 默认 main + latest UI)
```

- **dev**: 主开发分支，install.sh 默认 `dev`
- **beta**: 预发布分支，install.sh 默认 `beta`，UI 用 `preview` pre-release
- **main**: 正式分支，install.sh 默认 `main`，UI 用 `latest`

## 关键前提：分支专属代码预处理

`install.sh` 是**分支专属**文件——每个分支的默认值不同：

| 分支 | `BRANCH=` 默认 | `RELEASE_TAG=` 默认 |
|------|---------------|---------------------|
| dev | `dev` | `latest` |
| beta | `beta` | `preview` |
| main | `main` | `latest` |

**合并前，必须在 release 分支里把 install.sh 改成目标分支的默认值**，再合并到目标分支。否则 dev 的 install.sh（默认 dev）会覆盖 beta/main 的，导致 curl 对分支脚本却装错分支。

预处理步骤（在 release 分支内）：
```bash
# 改 install.sh 两处默认值
sed -i 's/MNEMOSYNC_BRANCH:-[a-z]*/MNEMOSYNC_BRANCH:-<目标分支>/' install.sh
sed -i 's/MNEMOSYNC_RELEASE_TAG:-[a-z]*/MNEMOSYNC_RELEASE_TAG:-<目标tag>/' install.sh
# 确认改对了
grep -E 'MNEMOSYNC_BRANCH:-|MNEMOSYNC_RELEASE_TAG:-' install.sh
```

---

## 一、Beta 预发布 (dev → beta)

把 dev 的最新改动合并到 beta，供服务器预发布测试。

### 1. 确认版本号

读取 `pyproject.toml` 的 `version`，beta 版本应为 `X.Y.Z-beta.N`（如 `0.4.0-beta.2`）。若与 dev 相同，先升到下一个 beta 号。

### 2. 冒烟测试

```bash
uv run pytest tests/ --no-cov -x -q
cd ui && npm install --legacy-peer-deps && npm run build && cd ..
```

两项都必须通过才能继续。

### 3. 创建 release 分支 + 预处理

```bash
git checkout dev
git pull origin dev
# 创建 beta release 分支
git checkout -b release/beta-v{VERSION}
# 预处理: 把 install.sh 改成 beta 默认值
sed -i 's/MNEMOSYNC_BRANCH:-[a-z]*/MNEMOSYNC_BRANCH:-beta/' install.sh
sed -i 's/MNEMOSYNC_RELEASE_TAG:-[a-z]*/MNEMOSYNC_RELEASE_TAG:-preview/' install.sh
grep -E 'MNEMOSYNC_BRANCH:-|MNEMOSYNC_RELEASE_TAG:-' install.sh   # 确认 = beta / preview
git add -A
git commit -m "release: beta v{VERSION}"
```

### 4. 合并到 beta 并推送

```bash
git checkout beta
git merge --no-ff release/beta-v{VERSION} -m "release: beta v{VERSION}"
git push origin refs/heads/beta:refs/heads/beta
```

推送会触发 `preview.yml` 工作流，自动构建 UI 发布为 `preview` pre-release。

### 5. 清理 release 分支

```bash
git checkout dev
git branch -d release/beta-v{VERSION}
```

### 6. 验证 preview 工作流

```bash
gh run watch $(gh run list --workflow=preview.yml --limit=1 --json databaseId --jq '.[0].databaseId')
gh release view preview --json tagName,isPrerelease,assets
```

**必须确认 `preview` pre-release 存在且含 `ui-dist.tar.gz`。**

---

## 二、Main 正式发布 (beta → main)

把 beta 发布的版本合并到 main 并打正式 tag。

### 0. 前置守卫：必须已发 beta

**检查 beta 已发布且测试通过：**
```bash
gh release view preview --json tagName,isPrerelease,assets
# 确认 preview pre-release 存在
```

**若尚未发布 beta，提醒用户先走「一、Beta 预发布」，禁止直接 dev → main。**

### 1. 确认版本号

读取 `pyproject.toml` 的 `version`，main 版本应为 `X.Y.Z`（去掉 `-beta.N`，如 `0.4.0`）。若 beta 版是 `0.4.0-beta.2`，则 main 版为 `0.4.0`。

### 2. 冒烟测试

同上的冒烟测试，必须通过。

### 3. 创建 release 分支 + 预处理

```bash
git checkout beta
git pull origin beta
# 创建 main release 分支
git checkout -b release/v{VERSION}
# 预处理: 去掉 beta 后缀, 把 install.sh 改成 main 默认值
sed -i 's/version = "X.Y.Z-beta.N"/version = "X.Y.Z"/' pyproject.toml
sed -i 's/MNEMOSYNC_BRANCH:-[a-z]*/MNEMOSYNC_BRANCH:-main/' install.sh
sed -i 's/MNEMOSYNC_RELEASE_TAG:-[a-z]*/MNEMOSYNC_RELEASE_TAG:-latest/' install.sh
grep -E '^version = "|MNEMOSYNC_BRANCH:-|MNEMOSYNC_RELEASE_TAG:-' pyproject.toml install.sh   # 确认
git add -A
git commit -m "release: v{VERSION}"
```

### 4. 合并到 main 并推送

```bash
git checkout main
git merge --no-ff release/v{VERSION} -m "release: v{VERSION}"
git push origin main
```

### 5. 打 tag 并推送

```bash
git tag -a v{VERSION} -m "Mnemosync v{VERSION}"
git push origin v{VERSION}
```

触发 `release.yml` 工作流，自动构建 UI 创建 `v{VERSION}` Release。

### 6. 清理 release 分支

```bash
git checkout beta
git branch -d release/v{VERSION}
```

### 7. 验证 GitHub Actions

```bash
gh run watch $(gh run list --workflow=release.yml --limit=1 --json databaseId --jq '.[0].databaseId')
gh release view v{VERSION} --json tagName,publishedAt,assets
```

**必须确认 `conclusion` 为 `success` 才算发布完成。**

如果 workflow 失败：
1. 查看失败日志：`gh run view <run-id> --log-failed`
2. 在 beta 分支修复问题，重新走发布流程
3. 删除失败的 tag，重新打 tag 推送

---

## 回滚

### 回滚 tag / release（main 或 beta）

```bash
git tag -d v{VERSION}          # 或 git tag -d preview
git push origin :refs/tags/v{VERSION}
gh release delete v{VERSION} --yes
```

### 回滚 merge

```bash
git checkout main              # 或 beta
git reset --hard HEAD~1
git push origin main --force-with-lease
```

**绝不要对 `main` 执行无 force-with-lease 的 force-push。**

---

## 注意事项

- **绝不要直接 dev → main**：必须先 beta 预发布测试，main 发布前检查 `preview` pre-release 存在
- **绝不要对 `main` 执行 force-push**（若必须，用 `--force-with-lease`）
- **分支专属 install.sh**：合并前必须在 release 分支预处理，改对目标分支的默认值
- **tag/分支同名陷阱**：`preview` tag 与 `beta` 分支不同名，但若本地残留过时 tag（如曾用 `beta` 作 tag），会造成 `git log beta` 歧义。遇到 "refname 'beta' is ambiguous" 时：
  ```bash
  git show-ref | grep -E 'beta$'        # 看是否有 refs/tags/beta
  git tag -d beta                        # 删除过时的本地 tag
  ```
- `install.sh` 会自动拉取最新 release，无需通知用户
- 版本号规则：低版本 < Beta版 < 正式版（`0.3.5 < 0.4.0-beta.1 < 0.4.0`），安装脚本会拒绝降级