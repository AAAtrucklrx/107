import * as Tabs from "@radix-ui/react-tabs";
import { CalendarRange, ExternalLink, Landmark, MapPin, Search, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Limitations, SourceBadge, WorkspaceError, WorkspaceLoading } from "../components/WorkspacePrimitives";
import { apiGet } from "../lib/api";
import type { CampusActivities, CampusActivity, CampusServices } from "../types";

function readableTime(value: unknown): string {
  if (!value) return "时间待核验";
  const raw = String(value);
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "short", hour: "2-digit", minute: "2-digit" }).format(parsed);
}

function activityTitle(item: CampusActivity): string {
  return String(item.title || item.name || "未命名活动");
}

export function CampusWorkspace() {
  const [services, setServices] = useState<CampusServices | null>(null);
  const [activities, setActivities] = useState<CampusActivities | null>(null);
  const [query, setQuery] = useState("");
  const [serviceCategory, setServiceCategory] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (search = "", category = "") => {
    setError(null);
    setLoading(true);
    try {
      const parameters = new URLSearchParams();
      if (search) parameters.set("query", search);
      if (category) parameters.set("category", category);
      const suffix = parameters.size ? `?${parameters.toString()}` : "";
      const [serviceResult, activityResult] = await Promise.all([
        apiGet<CampusServices>(`/campus/services${suffix}`),
        apiGet<CampusActivities>(`/campus/activities${suffix}`),
      ]);
      setServices(serviceResult);
      setActivities(activityResult);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法加载校园服务。" );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const categories = useMemo(() => services?.categories ?? [], [services]);
  if (error && !services) return <WorkspaceError message={error} onRetry={() => void load(query, serviceCategory)} />;
  if (!services || !activities) return <WorkspaceLoading label="正在汇集校园公开信息" />;

  return (
    <Tabs.Root className="workspace-tabs campus-workspace" defaultValue="services">
      <header className="workspace-header campus-header">
        <div><span className="eyebrow">公开信息工作区</span><h1>校园服务</h1></div>
        <form className="campus-search" onSubmit={(event) => { event.preventDefault(); void load(query, serviceCategory); }}>
          <Search size={17} aria-hidden="true" />
          <input aria-label="搜索校园服务和活动" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索入口或活动" />
          <button type="submit" disabled={loading}>搜索</button>
        </form>
      </header>
      <div className="campus-toolbar">
        <Tabs.List className="tabs-list tabs-list--compact" aria-label="校园信息类型">
          <Tabs.Trigger value="services"><Landmark size={15} />办事入口</Tabs.Trigger>
          <Tabs.Trigger value="activities"><Sparkles size={15} />活动</Tabs.Trigger>
        </Tabs.List>
        <label className="compact-select">类别
          <select value={serviceCategory} onChange={(event) => { const value = event.target.value; setServiceCategory(value); void load(query, value); }}>
            <option value="">全部</option>
            {categories.map((category) => <option key={category} value={category}>{category}</option>)}
          </select>
        </label>
      </div>
      {error && <div className="workspace-inline-error" role="alert">{error}</div>}
      <div className="tabs-scroll">
        <Tabs.Content value="services" className="campus-content">
          <div className="content-source-row"><span>{services.items.length} 个经过配置审核的入口</span><SourceBadge source={services.source} /></div>
          {services.items.length ? (
            <div className="service-grid">
              {services.items.map((service) => (
                <article className="service-item" key={`${service.name}-${service.url}`}>
                  <div className="service-item__icon"><Landmark size={19} /></div>
                  <div><span>{service.category}</span><h2>{service.name}</h2><p>{service.description}</p></div>
                  <a href={service.url} target="_blank" rel="noreferrer" aria-label={`打开 ${service.name}`}><ExternalLink size={17} /></a>
                </article>
              ))}
            </div>
          ) : <div className="empty-result">没有匹配的校园入口</div>}
        </Tabs.Content>
        <Tabs.Content value="activities" className="campus-content">
          <div className="content-source-row"><span>{activities.items.length} 条公开活动</span><SourceBadge source={activities.source} /></div>
          {activities.items.length ? (
            <div className="activity-grid">
              {activities.items.map((activity, index) => {
                const url = typeof activity.url === "string" ? activity.url : "";
                return (
                  <article className="activity-item" key={String(activity.id ?? `${activityTitle(activity)}-${index}`)}>
                    <div className="activity-item__date"><CalendarRange size={16} /><span>{readableTime(activity.start_time || activity.deadline)}</span></div>
                    <span className="activity-item__category">{String(activity.category || "校园活动")}</span>
                    <h2>{activityTitle(activity)}</h2>
                    {activity.description && <p>{String(activity.description)}</p>}
                    <div className="activity-item__footer">
                      <span><MapPin size={14} />{String(activity.location || "地点待核验")}</span>
                      {url && <a href={url} target="_blank" rel="noreferrer">查看来源<ExternalLink size={13} /></a>}
                    </div>
                  </article>
                );
              })}
            </div>
          ) : <div className="empty-result">暂无匹配的公开活动</div>}
          <Limitations items={activities.limitations} />
        </Tabs.Content>
      </div>
    </Tabs.Root>
  );
}
