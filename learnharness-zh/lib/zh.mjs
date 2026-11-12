// 中文教学话术模板：把"对模型说什么"从代码里拿出来，方便改、方便审。
// 这些字符串会作为用户消息发给 LearnHarness，老师的回复语言/风格由它引导。

export const ZH_RULES = [
	"全程用简体中文回答，术语第一次出现时给出中文 + 英文原词。",
	"一次只走一个推理步，不要一口气讲完整章。",
	"假设我按国内教材/考纲的进度：先给结论，再给依据，最后给一个自测题。",
	"不确定的地方直接说不确定，不要编造页码、版本号或政策原文。",
	"每段结尾用一句话总结这一步到底成立了什么。",
].join("\n");

export function lessonPrompt({ topic, goal, level, minutes }) {
	const lines = [
		`我想学：${topic}`,
		goal ? `我的目标：${goal}` : "",
		level ? `我目前的水平：${level}` : "",
		minutes ? `我这次大概能投入 ${minutes} 分钟。` : "",
		"",
		ZH_RULES,
		"",
		"先别开讲：先用 3 个问题测我现在的理解边界（我答完再继续）。",
	].filter(Boolean);
	return lines.join("\n");
}

export function clarifyPrompt(question) {
	return [`补充一个具体情况：${question}`, "", ZH_RULES].join("\n");
}

export function practicePrompt({ topic, weak } = {}) {
	const lines = [
		`现在开始练习${topic ? "：" + topic : ""}。`,
		weak ? `我最近容易错的是：${weak}。` : "",
		"",
		"要求：",
		"1. 一次只给一道题，不要提前给答案。",
		"2. 题目要能逼我自己把知识点想起来，不要只考背定义。",
		"3. 我答完你再判，判的时候指出错在哪一步，而不是只说对错。",
	].filter(Boolean);
	return lines.join("\n");
}

export function reviewPrompt() {
	return [
		"把今天这一节复盘成一份可以直接抄进错题本的内容：",
		"1. 今天建立/修正了哪几个命题（一句话一条）；",
		"2. 我卡住的步骤及其原因；",
		"3. 3 道自测题（只给题，不给答案，答案放到最后并用 --- 隔开）。",
	].join("\n");
}
