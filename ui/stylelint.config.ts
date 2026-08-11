/**
 * Stylelint 配置：Vue SFC + SCSS，属性按 RECESS 顺序自动排序（lint 时 --fix 生效）。
 *
 * - stylelint-config-standard-scss: SCSS 标准规则集，独立 .scss 文件用 postcss-scss 解析
 * - stylelint-config-recommended-vue/scss: 扩展 stylelint-config-html，
 *   用 postcss-html 解析 Vue 单文件组件的 <style lang="scss"> 块
 * - stylelint-config-recess-order: 属性排序（positioning → box model → typography → visual → misc）
 */
export default {
	extends: [
		'stylelint-config-standard-scss',
		'stylelint-config-recommended-vue/scss',
		'stylelint-config-recess-order',
	],
	rules: {
		// 项目使用 BEM 命名（Element Plus 的 .el-*__* / .el-*--*，以及业务上的 .xxx__inner 等），
		// 默认 kebab-case 不允许 "__" / "--"，放开以兼容 BEM。
		'selector-class-pattern': '^[a-z][a-z0-9-]*(__[a-z0-9-]+)*(--[a-z0-9-]+)?$',
		// 不允许单行声明块（.foo { a: 1; b: 2 } 一律展开为多行，0 = 单行最多 0 条声明）。
		'declaration-block-single-line-max-declarations': 0,
		// value-keyword-case 会把自定义属性（--el-font-family）里未加引号的字体名强制小写，
		// BlinkMacSystemFont 等大小写敏感，需保留原样。
		'value-keyword-case': ['lower', { ignoreKeywords: ['BlinkMacSystemFont', 'Helvetica', 'Arial'] }],
		// 颜色写法保持原样，不做现代化改写（#ffffff 不缩成 #fff、rgba() 不转 rgb()/%、alpha 不转百分比）。
		'color-hex-length': null,
		'color-function-notation': null,
		'alpha-value-notation': null,
		'color-function-alias-notation': null,
		// 保留 SCSS 变量之间的分组空行（不要求/不禁止连续 $ 变量间的空行）。
		'scss/dollar-variable-empty-line-before': null,
	},
	ignoreFiles: ['**/dist/**', '**/coverage/**', '**/node_modules/**'],
}