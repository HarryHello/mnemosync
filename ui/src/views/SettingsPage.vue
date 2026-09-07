<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, ElCol, ElRow } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { changePassword, checkUpdate, listVersions, restartService, setToken, upgradeService } from '@/api/client'
import type { ReleaseInfo, UpdateCheckResult } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useRouter } from 'vue-router'

const router = useRouter()
const authStore = useAuthStore()

const formRef = ref<FormInstance | null>(null)
const submitting = ref(false)
const restarting = ref(false)
const upgrading = ref(false)
const updateChecking = ref(false)
const updateResult = ref<UpdateCheckResult | null>(null)
const versions = ref<ReleaseInfo[]>([])
const currentVersion = ref('')
const selectedVersion = ref('')

const form = reactive({
  old_password: '',
  new_password: '',
  confirm: '',
})

const rules: FormRules = {
  old_password: [{ required: true, message: '请输入当前密码', trigger: 'blur' }],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 6, message: '至少 6 位', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (_r, value: string, cb) => {
        if (value !== form.new_password) cb(new Error('两次输入不一致'))
        else cb()
      },
      trigger: 'blur',
    },
  ],
}

async function onSubmit() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    await changePassword({
      old_password: form.old_password,
      new_password: form.new_password,
    })
    ElMessage.success('密码已更新, 请重新登录')
    setToken(null)
    router.push('/login')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    submitting.value = false
  }
}

async function onUpdateCheck() {
  updateChecking.value = true
  try {
    updateResult.value = await checkUpdate()
    if (!updateResult.value.update_available) {
      ElMessage.success('已是最新版本')
    }
    // 加载版本列表供选择升级目标
    try {
      const v = await listVersions()
      currentVersion.value = v.current_version
      versions.value = v.releases
      // 默认选中最新版
      if (v.releases.length) selectedVersion.value = v.releases[0]?.version ?? ''
    } catch {
      // 版本列表加载失败不阻塞主流程
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    updateChecking.value = false
  }
}

async function onUpgrade() {
  const target = selectedVersion.value || updateResult.value?.latest_version
  if (!target) return
  try {
    await ElMessageBox.confirm(
      `将升级到 ${target}，升级后需要重启服务。确认升级吗？`,
      '升级',
      {
        confirmButtonText: '确认升级',
        cancelButtonText: '取消',
        type: 'info',
      },
    )
  } catch {
    return
  }

  upgrading.value = true
  try {
    const res = await upgradeService(target)
    ElMessage.success(res.message || '升级已启动')
    ElMessage.info('请稍后刷新页面，然后重启服务')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    upgrading.value = false
  }
}

onMounted(onUpdateCheck)

async function onRestart() {
  try {
    await ElMessageBox.confirm(
      '重启服务会中断当前连接, 请稍后刷新页面。确认重启吗?',
      '重启服务',
      {
        confirmButtonText: '确认重启',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return // 用户取消
  }

  restarting.value = true
  try {
    const res = await restartService()
    ElMessage.success(res.message || '服务重启中...')
    ElMessage.info('请稍后重启完成后刷新页面')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    restarting.value = false
  }
}
</script>

<template>
  <div class="page-container">
    <h2 class="page-title">设置</h2>
    <p class="page-subtitle">账户与偏好设置。更多配置项将陆续开放。</p>

    <el-card class="section">
      <template #header>
        <div class="card-header">
          <span>账号信息</span>
        </div>
      </template>

      <el-form label-width="120px" style="max-width: 480px">
        <el-form-item label="当前用户名">
          <el-input :model-value="authStore.user?.username ?? ''" disabled />
        </el-form-item>
      </el-form>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="如需修改用户名"
        description="面板不支持修改用户名, 请在服务器本机运行 `mnemosync login` 进入交互式 CLI 修改。"
      />
    </el-card>

    <el-card class="section">
      <template #header>
        <div class="card-header">
          <span>修改密码</span>
        </div>
      </template>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="120px"
        style="max-width: 480px"
      >
        <el-form-item label="当前密码" prop="old_password">
          <el-input
            v-model="form.old_password"
            type="password"
            show-password
            autocomplete="current-password"
          />
        </el-form-item>
        <el-form-item label="新密码" prop="new_password">
          <el-input
            v-model="form.new_password"
            type="password"
            show-password
            autocomplete="new-password"
          />
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirm">
          <el-input
            v-model="form.confirm"
            type="password"
            show-password
            autocomplete="new-password"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="submitting" @click="onSubmit">更新密码</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-row :gutter="16" class="settings-row">
      <el-col :span="12">
        <el-card class="section settings-card">
          <template #header>
            <div class="card-header">
              <span>服务</span>
            </div>
          </template>

          <el-alert
            type="warning"
            :closable="false"
            show-icon
            title="重启服务会短暂中断连接"
            description="重启完成后需要刷新页面重新加载。"
          />
          <div class="restart-wrapper">
            <el-button
              type="danger"
              :loading="restarting"
              @click="onRestart">
                重启服务
            </el-button>
          </div>
        </el-card>
      </el-col>

      <el-col :span="12">
        <el-card class="section settings-card">
          <template #header>
            <div class="card-header">
              <span>版本更新</span>
            </div>
          </template>

          <div v-if="updateChecking" class="update-status">
            <el-skeleton :rows="1" animated />
          </div>
          <div v-else class="update-version-select">
            <el-alert type="info" :closable="false" show-icon
              :title="`当前版本: ${currentVersion || (updateResult?.current_version || '未知')}`"
            />
            <div class="update-select-row">
              <el-select v-model="selectedVersion"
                placeholder="选择要升级到的版本"
                style="flex: 1"
                filterable
              >
                <el-option v-for="r in versions" :key="r.version" :value="r.version"
                  :label="`${r.version}${r.is_prerelease ? ' (beta)' : ''}`"
                >
                  <div class="version-option">
                    <div class="version-option-title">
                      {{ r.version }}{{ r.is_prerelease ? ' (beta)' : '' }}
                    </div>
                    <div v-if="r.description" class="version-option-desc">
                      {{ r.description.trim().split('\n')[0] }}
                    </div>
                  </div>
                </el-option>
              </el-select>
              <el-button type="primary" :loading="upgrading" @click="onUpgrade">
                升级
              </el-button>
            </div>
            <div class="update-actions">
              <el-button @click="onUpdateCheck" :loading="updateChecking" size="small">
                重新检查
              </el-button>
              <el-button v-if="updateResult?.url" tag="a" :href="updateResult.url" target="_blank" link size="small">
                查看更新日志
              </el-button>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style lang="scss" scoped>
.section {
  margin-bottom: $space-4;
}

.settings-row {
  align-items: stretch;
}

.settings-card {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.card-header {
  display: flex;
  gap: $space-2;
  align-items: center;
}

.restart-wrapper {
  display: flex;
  justify-content: center;
  margin-top: $space-3;
}

.update-actions {
  display: flex;
  gap: $space-2;
  align-items: center;
  margin-top: $space-3;
}

.update-latest {
  display: flex;
  flex-direction: column;
}

.update-version-select {
  display: flex;
  flex-direction: column;
  gap: $space-3;
}

.update-select-row {
  display: flex;
  gap: $space-2;
  align-items: center;
}

.version-option {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}

.version-option-title {
  font-weight: 500;
}

.version-option-desc {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}
</style>
