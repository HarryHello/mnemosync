<script setup lang="ts">
/**
 * 身份管理 + 关系状态 合并页 (v0.3.3).
 *
 * 两个标签页:
 * - 身份管理: 身份策略 → 参与者 → 用户组
 * - 关系状态: 当前人格与各用户的关系详情
 */
import { ref } from 'vue'
import StrategyTab from '@/components/identity/StrategyTab.vue'
import ActorTab from '@/components/identity/ActorTab.vue'
import GroupTab from '@/components/identity/GroupTab.vue'
import RelationshipTab from '@/components/relationships/RelationshipTab.vue'

const activeTab = ref('identity')
const identitySubTab = ref('strategies')
const strategyTab = ref<InstanceType<typeof StrategyTab> | null>(null)
const actorTab = ref<InstanceType<typeof ActorTab> | null>(null)
const groupTab = ref<InstanceType<typeof GroupTab> | null>(null)

function onSubTabChange(tab: string | number) {
  identitySubTab.value = tab as string
  if (tab === 'strategies') void strategyTab.value?.refresh()
  else if (tab === 'actors') void actorTab.value?.refresh()
  else if (tab === 'groups') void groupTab.value?.refresh()
}
</script>

<template>
  <div class="page-container">
    <el-tabs v-model="activeTab" class="page-tabs">
      <el-tab-pane label="身份管理" name="identity">
        <div class="tab-head">
          <h3 class="tab-title">身份管理</h3>
          <p class="tab-subtitle">管理身份识别策略、跨平台参与者与用户组。同一用户在不同平台的账号可绑定到同一用户组，共享记忆与关系。</p>
        </div>
        <el-card>
          <el-tabs v-model="identitySubTab" @tab-change="onSubTabChange">
            <el-tab-pane label="身份策略" name="strategies" lazy>
              <StrategyTab ref="strategyTab" />
            </el-tab-pane>
            <el-tab-pane label="参与者" name="actors" lazy>
              <ActorTab ref="actorTab" />
            </el-tab-pane>
            <el-tab-pane label="用户组" name="groups" lazy>
              <GroupTab ref="groupTab" />
            </el-tab-pane>
          </el-tabs>
        </el-card>
      </el-tab-pane>
      <el-tab-pane label="关系状态" name="relationships">
        <div class="tab-head">
          <h3 class="tab-title">关系状态</h3>
          <p class="tab-subtitle">当前人格与各用户的亲密度、信任度及称呼演化记录。每位用户的关系独立维护，可在运行期自然演化。</p>
        </div>
        <RelationshipTab :active="activeTab === 'relationships'" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style lang="scss" scoped>
.tab-head {
  display: flex;
  flex-direction: column;
  gap: $space-1;
  margin-bottom: $space-4;
}

.tab-title {
  font-size: 18px;
  font-weight: 600;
  margin: 0;
}

.tab-subtitle {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin: 0;
}
</style>
