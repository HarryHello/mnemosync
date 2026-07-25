<script setup lang="ts">
/**
 * 身份管理页 (v0.3.0).
 *
 * 三个维度: 身份策略 (如何识别) → 参与者 (识别出的账号) → 用户组 (跨平台归一).
 */
import { ref } from 'vue'
import PageHeader from '@/components/common/PageHeader.vue'
import StrategyTab from '@/components/identity/StrategyTab.vue'
import ActorTab from '@/components/identity/ActorTab.vue'
import GroupTab from '@/components/identity/GroupTab.vue'

const activeTab = ref('strategies')
const strategyTab = ref<InstanceType<typeof StrategyTab> | null>(null)
const actorTab = ref<InstanceType<typeof ActorTab> | null>(null)
const groupTab = ref<InstanceType<typeof GroupTab> | null>(null)

function onTabChange(tab: string | number) {
  // 切换标签时刷新目标数据 (其他标签的操作可能改变了共享状态, 如绑定)
  if (tab === 'strategies') void strategyTab.value?.refresh()
  else if (tab === 'actors') void actorTab.value?.refresh()
  else if (tab === 'groups') void groupTab.value?.refresh()
}
</script>

<template>
  <div class="page-container">
    <PageHeader
      title="身份管理"
      subtitle="多用户身份体系 (v0.3.0): 策略决定如何从请求中识别参与者; 用户组把跨平台的参与者归一到同一个人, 共享记忆与关系; 未绑定策略的接入进入非归属模式。"
    />

    <el-card>
      <el-tabs v-model="activeTab" @tab-change="onTabChange">
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
  </div>
</template>
