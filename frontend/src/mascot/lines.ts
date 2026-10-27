export const mascotLines = {
  idle: "我在这儿，陪你慢慢来。",
  think: "奶蛙正在翻…",
  sleep: "眯一会儿，叫我就醒。",
  success: ["查好啦！一起看看。", "嘿嘿，又解决一件事！", "好耶，答案来啦！"],
  laugh: ["哈哈哈哈，笑到肚子疼！", "你一戳，我就忍不住啦！", "快乐这件事，包在我身上！"],
  error: "诶？没查到，我们再试试。",
  eat: "干饭也要认真！",
};
export function randomLine(lines: string[]) { return lines[Math.floor(Math.random() * lines.length)]; }
export function greeting(date = new Date()) {
  const hour = date.getHours();
  if (hour < 6) return "夜深啦，忙完记得休息。";
  if (hour < 12) return "早上好呀！今天也一起慢慢来。";
  if (hour < 18) return "下午好呀！有我陪着你。";
  return "晚上好呀！今天也辛苦啦。";
}
export function localDay(date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
