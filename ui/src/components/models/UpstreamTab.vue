<!-- 上游 API 工作台 (参考 AstrBot ProviderPage 左右结构).

左侧: 服务商来源列表 (新增/选中/删除); 右侧: 选中服务商的
「设置」+「模型配置」就地编辑。创建服务后自动选中, 直接在右侧配模型。
角色绑定 (main/assist/embedding/rerank) 仍在「模型管理」tab。
-->
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { Delete, Plus, Refresh, Setting } from '@element-plus/icons-vue'
import {
  listUpstreamServices,
  createUpstreamService,
  updateUpstreamService,
  deleteUpstreamService,
  listUpstreamAvailableModels,
} from '@/api/client'
import type { UpstreamService } from '@/types/api'
import ModelRegistrySection from './ModelRegistrySection.vue'

const services = ref<UpstreamService[]>([])
const loading = ref(false)

// ── 选择态 ─────────────────────────────────────────────────────────────────
const selectedId = ref<string>('')
const selected = computed(() => services.value.find((s) => s.id === selectedId.value) ?? null)

// 设置表单 (就地编辑)
const settingForm = reactive({ base_url: '', api_key: '', api_format: 'openai' })
const dirty = ref(false)
const savingSettings = ref(false)

// ── 新增对话框 ─────────────────────────────────────────────────────────────
const createDialog = ref(false)
const createRef = ref<FormInstance | null>(null)
const createForm = reactive({ id: '', base_url: '', api_key: '', api_format: 'openai' })
const createSubmitting = ref(false)

const createRules: FormRules = {
  id: [{ required: true, message: '请填写服务 ID', trigger: 'blur' }],
  base_url: [
    { required: true, message: '请填写 Base URL', trigger: 'blur' },
    { pattern: /^https?:\/\//, message: 'URL 必须以 http:// 或 https:// 开头', trigger: 'blur' },
  ],
  api_key: [{ required: true, message: '请填写 API Key', trigger: 'blur' }],
}

// ──────────────────────────────────────────────────────────────────────────
function resetSettings() {
  const s = selected.value
  if (!s) return
  settingForm.base_url = s.base_url
  settingForm.api_key = ''
  settingForm.api_format = s.api_format || 'openai'
  dirty.value = false
}

function selectService(id: string) {
  selectedId.value = id
  resetSettings()
}

async function refresh() {
  loading.value = true
  try {
    services.value = await listUpstreamServices()
    if (selectedId.value && !services.value.some((s) => s.id === selectedId.value)) {
      selectedId.value = ''
    }
    if (selectedId.value) resetSettings()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  createForm.id = ''
  createForm.base_url = ''
  createForm.api_key = ''
  createForm.api_format = 'openai'
  createDialog.value = true
}

async function onCreate() {
  if (!createRef.value) return
  const ok = await createRef.value.validate().catch(() => false)
  if (!ok) return
  createSubmitting.value = true
  try {
    const createdId = createForm.id.trim()
    await createUpstreamService({
      id: createdId,
      base_url: createForm.base_url.trim(),
      api_key: createForm.api_key.trim(),
      api_format: createForm.api_format,
    })
    ElMessage.success('已创建, 可在右侧配置模型')
    createDialog.value = false
    await refresh()
    selectedId.value = createdId
    resetSettings()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    createSubmitting.value = false
  }
}

async function saveSettings() {
  const s = selected.value
  if (!s) return
  const payload: { base_url?: string; api_key?: string; api_format?: string } = {}
  const nextUrl = settingForm.base_url.trim()
  if (nextUrl && nextUrl !== s.base_url) payload.base_url = nextUrl
  if (settingForm.api_key.trim()) payload.api_key = settingForm.api_key.trim()
  if (settingForm.api_format !== (s.api_format || 'openai')) {
    payload.api_format = settingForm.api_format
  }
  if (!payload.base_url && !payload.api_key && !payload.api_format) {
    ElMessage.info('没有变更')
    return
  }
  savingSettings.value = true
  try {
    await updateUpstreamService(s.id, payload)
    ElMessage.success('已保存')
    dirty.value = false
    await refresh()
    // 刷新后保持选中
    selectedId.value = s.id
    resetSettings()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  } finally {
    savingSettings.value = false
  }
}

async function testService() {
  const s = selected.value
  if (!s) return
  try {
    const { models } = await listUpstreamAvailableModels(s.id)
    if (!models.length) {
      ElMessage.success('连接成功, 但未探测到可用模型')
      return
    }
    const list = models.map((m) => '<li class="mono">' + m + '</li>').join('')
    await ElMessageBox.alert(
      '<ul style="margin:0;padding-left:18px;max-height:320px;overflow:auto">' + list + '</ul>',
      '可用模型 (' + s.id + ')',
      {
        dangerouslyUseHTMLString: true,
        confirmButtonText: '关闭',
        customClass: 'test-models-box',
      },
    )
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

async function deleteService(svc: UpstreamService) {
  try {
    await ElMessageBox.confirm(
      '将删除服务 "' + svc.id + '", 其注册表模型随之级联删除, 引用它的角色绑定也会失效。确认删除？',
      '删除上游服务',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteUpstreamService(svc.id)
    ElMessage.success('已删除')
    if (selectedId.value === svc.id) selectedId.value = ''
    await refresh()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : String(err))
  }
}

onMounted(refresh)

defineExpose({ refresh })
</script>

<template>
  <div class="tab-head">
    <div>
      <h3 class="tab-title">上游 API</h3>
      <p class="tab-subtitle">
        管理 Mnemosync 使用的上游服务商 (如 DashScope / OpenRouter / 本地网关)。左侧选择服务商,
        右侧就地编辑凭证与模型配置; 按角色绑定去『模型管理』tab。
      </p>
    </div>
  </div>
  <el-card>
    <div class="provider-workbench" v-loading="loading">
      <!-- ── 左侧: 服务商来源列表 ─────────────────────────────────────────── -->
      <div class="provider-sidebar">
        <div class="sidebar-head">
          <div class="sidebar-title">上游 API</div>
          <div class="sidebar-actions">
            <el-button size="small" circle @click="refresh">
              <el-icon><Refresh /></el-icon>
            </el-button>
            <el-button size="small" type="primary" circle @click="openCreate">
              <el-icon><Plus /></el-icon>
            </el-button>
          </div>
        </div>

        <div class="source-list">
          <div
            v-for="svc in services"
            :key="svc.id"
            class="source-item"
            :class="{ active: svc.id === selectedId }"
            @click="selectService(svc.id)"
          >
            <div class="source-title">{{ svc.id }}</div>
            <div class="source-sub mono">{{ svc.base_url }}</div>
            <div class="source-tools">
              <el-tag size="small" type="info">{{ svc.api_format || 'openai' }}</el-tag>
              <el-icon class="del" @click.stop="deleteService(svc)"><Delete /></el-icon>
            </div>
          </div>
        </div>

        <el-empty
          v-if="!services.length && !loading"
          description="尚未配置上游服务, 点右上角 + 新增"
          :image-size="48"
        />
      </div>

      <div class="provider-divider"></div>

      <!-- 右侧: 选中服务商详情 -->
      <div class="provider-main">
        <div v-if="selected" class="config-shell" :key="selected.id">
          <div class="config-header">
            <div class="config-headline">
              <div class="config-title">{{ selected.id }}</div>
              <div class="config-subtitle mono">{{ selected.base_url }}</div>
            </div>
            <div class="config-actions">
              <el-button size="small" @click="testService">测试</el-button>
              <el-button
                size="small"
                type="danger"
                plain
                @click="selected && deleteService(selected)"
              >
                删除
              </el-button>
            </div>
          </div>

          <el-divider />

          <!-- ① 上游设置 -->
          <section class="provider-section">
            <div class="section-head">
              <el-icon><Setting /></el-icon>
              <span class="section-title">上游设置</span>
            </div>
            <el-form label-width="90px" class="settings-form">
              <el-form-item label="Base URL">
                <el-input
                  v-model="settingForm.base_url"
                  @input="dirty = true"
                  placeholder="https://your-provider.com/v1"
                />
              </el-form-item>
              <el-form-item label="API Key">
                <el-input
                  v-model="settingForm.api_key"
                  type="password"
                  show-password
                  @input="dirty = true"
                  placeholder="留空保持不变"
                />
              </el-form-item>
              <el-form-item label="API 格式">
                <el-select
                  v-model="settingForm.api_format"
                  style="width: 100%"
                  @change="dirty = true"
                >
                  <el-option label="OpenAI 兼容" value="openai" />
                  <el-option label="Anthropic" value="anthropic" />
                  <el-option label="OpenAI Responses API" value="responses" />
                </el-select>
                <div class="form-tip">
                  OpenAI 兼容适用于大多数服务商; Anthropic 用原生 Claude API; Responses 用于 OpenAI
                  新版接口。
                </div>
              </el-form-item>
              <el-form-item>
                <el-button
                  type="primary"
                  :loading="savingSettings"
                  :disabled="!dirty"
                  @click="saveSettings"
                >
                  保存设置
                </el-button>
                <el-button :disabled="!dirty" @click="resetSettings">还原</el-button>
              </el-form-item>
            </el-form>
          </section>

          <el-divider />

          <!-- ② 模型配置 -->
          <section class="provider-section">
            <div class="section-head">
              <span class="section-title">模型配置</span>
            </div>
            <ModelRegistrySection :service-id="selected?.id" />
          </section>
        </div>

        <div v-else class="provider-empty-state">
          <el-icon :size="40"><Setting /></el-icon>
          <p>从左侧选择一个上游服务开始配置; 没有服务商时点左上角 + 新增。</p>
        </div>
      </div>

      <!-- ── 新增服? 对话框 ────────────────────────────────────────────── -->
      <el-dialog v-model="createDialog" title="新增上游服务" width="480px">
        <el-form ref="createRef" :model="createForm" :rules="createRules" label-width="90px">
          <el-form-item label="服务 ID" prop="id">
            <el-input
              v-model="createForm.id"
              placeholder="例如: dashscope / openrouter"
              maxlength="64"
            />
          </el-form-item>
          <el-form-item label="Base URL" prop="base_url">
            <el-input v-model="createForm.base_url" placeholder="https://your-provider.com/v1" />
          </el-form-item>
          <el-form-item label="API Key" prop="api_key">
            <el-input
              v-model="createForm.api_key"
              type="password"
              show-password
              placeholder="sk-..."
            />
          </el-form-item>
          <el-form-item label="API 格式" prop="api_format">
            <el-select v-model="createForm.api_format" style="width: 100%">
              <el-option label="OpenAI 兼容" value="openai" />
              <el-option label="Anthropic" value="anthropic" />
              <el-option label="OpenAI Responses API" value="responses" />
            </el-select>
            <div class="form-tip">创建后自动选中, 在右侧配置模型。</div>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="createDialog = false">取消</el-button>
          <el-button type="primary" :loading="createSubmitting" @click="onCreate">创建</el-button>
        </template>
      </el-dialog>
    </div>
  </el-card>
</template>

<style lang="scss" scoped>
.tab-head {
  display: flex;
  gap: $space-4;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: $space-4;
}

.tab-title {
  margin: 0 0 $space-1;
  font-size: 18px;
  font-weight: 600;
}

.tab-subtitle {
  margin: 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.provider-workbench {
  display: flex;
  min-height: 480px;
  overflow: hidden;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

// ── 左列 ──────────────────────────────────────────────────────
.provider-sidebar {
  display: flex;
  flex-shrink: 0;
  flex-direction: column;
  width: 280px;
  background: var(--el-bg-color-page, #fafafa);
  border-right: 0;

  .sidebar-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: $space-2 $space-3;
    border-bottom: 1px solid var(--el-border-color-lighter);

    .sidebar-title {
      font-weight: 600;
    }
  }

  .source-list {
    flex: 1;
    padding: $space-2;
    overflow-y: auto;
  }

  .source-item {
    padding: $space-2;
    margin-bottom: $space-1;
    cursor: pointer;
    border: 1px solid transparent;
    border-radius: 6px;
    transition: background 0.15s;

    &:hover {
      background: var(--el-fill-color-light);
    }

    &.active {
      background: var(--el-color-primary-light-9);
      border-color: var(--el-color-primary-light-5);

      .source-title {
        color: var(--el-color-primary);
      }
    }

    .source-title {
      font-size: 14px;
      font-weight: 600;
    }

    .source-sub {
      margin: 2px 0 4px;
      overflow: hidden;
      text-overflow: ellipsis;
      font-size: 11px;
      color: var(--el-text-color-secondary);
      white-space: nowrap;
    }

    .source-tools {
      display: flex;
      align-items: center;
      justify-content: space-between;

      .del {
        font-size: 14px;
        color: var(--el-text-color-secondary);
        cursor: pointer;

        &:hover {
          color: var(--el-color-danger);
        }
      }
    }
  }
}

.provider-divider {
  width: 1px;
  background: var(--el-border-color-lighter);
}

// ── 右列 ──────────────────────────────────────────────────────
.provider-main {
  flex: 1;
  min-width: 0;
  padding: $space-3;
  overflow-y: auto;
}

.config-shell {
  .config-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;

    .config-headline {
      .config-title {
        font-size: 18px;
        font-weight: 700;
      }

      .config-subtitle {
        margin-top: 2px;
        font-size: 12px;
        color: var(--el-text-color-secondary);
      }
    }
  }

  .provider-section {
    .section-head {
      display: flex;
      gap: $space-1;
      align-items: center;
      margin-bottom: $space-2;

      .section-title {
        font-weight: 600;
      }
    }

    .settings-form {
      max-width: 620px;

      .form-tip {
        margin-top: 2px;
        font-size: 12px;
        color: var(--el-text-color-secondary);
      }
    }
  }
}

.provider-empty-state {
  display: flex;
  flex-direction: column;
  gap: $space-2;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 440px;
  color: var(--el-text-color-secondary);
}
</style>
