import * as Tabs from "@radix-ui/react-tabs";
import {
  BookOpen,
  Briefcase,
  CalendarRange,
  ChevronDown,
  CreditCard,
  ExternalLink,
  Globe2,
  GraduationCap,
  HeartPulse,
  KeyRound,
  Landmark,
  Library,
  MapPin,
  Megaphone,
  Search,
  Sparkles,
  UserRound,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { LaunchTile } from "../components/LaunchTile";
import { Limitations, SourceBadge, WorkspaceError, WorkspaceLoading } from "../components/WorkspacePrimitives";
import { apiGet } from "../lib/api";
import type { CampusActivities, CampusActivity, CampusServiceItem, CampusServices } from "../types";

type CampusTab = "services" | "activities";

const namedServiceIcons: Record<string, LucideIcon> = {
  "本科生综合教务系统": GraduationCap,
  "课程目录系统": BookOpen,
  "教务处官网": Landmark,
  "图书馆": Library,
  "校团委活动平台（young）": Megaphone,
  "统一身份认证（CAS）": KeyRound,
  "校园缴费平台": CreditCard,
  "就业指导中心": Briefcase,
  "校医院": HeartPulse,
};

const categoryServiceIcons: Record<string, LucideIcon> = {
  教务学习: BookOpen,
  个人事务: UserRound,
  生活服务: Landmark,
  就业发展: Briefcase,
  交流升学: GraduationCap,
  社区工具: Globe2,
};

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

function groupBy<T>(items: T[], getKey: (item: T) => string): Array<[string, T[]]> {
  const groups = new Map<string, T[]>();
  items.forEach((item) => {
    const key = getKey(item);
    groups.set(key, [...(groups.get(key) ?? []), item]);
  });
  return Array.from(groups.entries());
}

function readableHost(value: string): string {
  try {
    return new URL(value).hostname.replace(/^www\./, "");
  } catch {
    return "公开来源";
  }
}

function serviceIcon(service: CampusServiceItem): LucideIcon {
  return namedServiceIcons[service.name] ?? categoryServiceIcons[service.category] ?? Landmark;
}

function uniqueServices(items: CampusServiceItem[]): CampusServiceItem[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const key = item.url.trim().toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function requestSuffix(query: string, category: string): string {
  const parameters = new URLSearchParams();
  if (query.trim()) parameters.set("query", query.trim());
  if (category) parameters.set("category", category);
  return parameters.size ? `?${parameters.toString()}` : "";
}

function useMobileCatalog(): boolean {
  const [mobile, setMobile] = useState(() => (
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia("(max-width: 760px)").matches
      : false
  ));

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const query = window.matchMedia("(max-width: 760px)");
    const update = () => setMobile(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return mobile;
}

function ServicesSkeleton() {
  return (
    <div className="campus-services-skeleton" role="status" aria-label="正在载入校园入口">
      <div className="section-kicker skeleton-line" />
      <div className="launch-grid launch-grid--featured">
        {Array.from({ length: 8 }, (_, index) => (
          <div className="launch-tile launch-tile--skeleton" key={index} aria-hidden="true">
            <span className="skeleton-line skeleton-line--short" />
            <span className="skeleton-line skeleton-line--title" />
            <span className="skeleton-line" />
            <span className="skeleton-line skeleton-line--short" />
          </div>
        ))}
      </div>
    </div>
  );
}

export function CampusWorkspace() {
  const mobileCatalog = useMobileCatalog();
  const [activeTab, setActiveTab] = useState<CampusTab>("services");
  const [services, setServices] = useState<CampusServices | null>(null);
  const [activities, setActivities] = useState<CampusActivities | null>(null);
  const [serviceSearch, setServiceSearch] = useState("");
  const [serviceQuery, setServiceQuery] = useState("");
  const [serviceCategory, setServiceCategory] = useState("");
  const [activitySearch, setActivitySearch] = useState("");
  const [activityQuery, setActivityQuery] = useState("");
  const [activityCategory, setActivityCategory] = useState("");
  const [activityCategories, setActivityCategories] = useState<string[]>([]);
  const [openCategories, setOpenCategories] = useState<Set<string>>(() => new Set(["教务学习"]));
  const [serviceError, setServiceError] = useState<string | null>(null);
  const [activityError, setActivityError] = useState<string | null>(null);
  const [serviceLoading, setServiceLoading] = useState(true);
  const [activityLoading, setActivityLoading] = useState(true);

  const loadServices = useCallback(async (search = "", category = "") => {
    const cleanSearch = search.trim();
    setServiceQuery(cleanSearch);
    setServiceError(null);
    setServiceLoading(true);
    try {
      setServices(await apiGet<CampusServices>(`/campus/services${requestSuffix(cleanSearch, category)}`));
    } catch (reason) {
      setServiceError(reason instanceof Error ? reason.message : "无法加载校园入口。");
    } finally {
      setServiceLoading(false);
    }
  }, []);

  const loadActivities = useCallback(async (search = "", category = "") => {
    const cleanSearch = search.trim();
    setActivityQuery(cleanSearch);
    setActivityError(null);
    setActivityLoading(true);
    try {
      const result = await apiGet<CampusActivities>(`/campus/activities${requestSuffix(cleanSearch, category)}`);
      setActivities(result);
      const discovered = result.items
        .map((item) => String(item.category || "").trim())
        .filter(Boolean);
      setActivityCategories((current) => Array.from(new Set([...current, ...discovered])));
    } catch (reason) {
      setActivityError(reason instanceof Error ? reason.message : "无法加载校园活动。");
    } finally {
      setActivityLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadServices();
    void loadActivities();
  }, [loadActivities, loadServices]);

  const categories = services?.categories ?? [];
  const allServices = useMemo(() => uniqueServices(services?.items ?? []), [services]);
  const servicesFiltered = Boolean(serviceQuery || serviceCategory);
  const featuredServices = useMemo(
    () => allServices
      .filter((service) => service.featured)
      .sort((left, right) => (left.priority ?? Number.MAX_SAFE_INTEGER) - (right.priority ?? Number.MAX_SAFE_INTEGER)),
    [allServices],
  );
  const directoryServices = useMemo(
    () => servicesFiltered ? allServices : allServices.filter((service) => !service.featured),
    [allServices, servicesFiltered],
  );
  const serviceGroups = useMemo(
    () => groupBy(directoryServices, (service) => service.category || "其他入口"),
    [directoryServices],
  );
  const activityGroups = useMemo(
    () => groupBy(activities?.items ?? [], (activity) => String(activity.category || "校园活动")),
    [activities],
  );
  const activitiesFiltered = Boolean(activityQuery || activityCategory);

  const currentSearch = activeTab === "services" ? serviceSearch : activitySearch;
  const currentCategory = activeTab === "services" ? serviceCategory : activityCategory;
  const currentCategories = activeTab === "services" ? categories : activityCategories;
  const currentLoading = activeTab === "services" ? serviceLoading : activityLoading;

  const submitSearch = () => {
    if (activeTab === "services") void loadServices(serviceSearch, serviceCategory);
    else void loadActivities(activitySearch, activityCategory);
  };

  const changeCategory = (category: string) => {
    if (activeTab === "services") {
      setServiceCategory(category);
      void loadServices(serviceSearch, category);
    } else {
      setActivityCategory(category);
      void loadActivities(activitySearch, category);
    }
  };

  const clearFilters = () => {
    if (activeTab === "services") {
      setServiceSearch("");
      setServiceCategory("");
      void loadServices();
    } else {
      setActivitySearch("");
      setActivityCategory("");
      void loadActivities();
    }
  };

  const toggleCategory = (category: string) => {
    setOpenCategories((current) => {
      const next = new Set(current);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      return next;
    });
  };

  return (
    <Tabs.Root className="workspace-tabs campus-workspace" value={activeTab} onValueChange={(value) => setActiveTab(value as CampusTab)}>
      <header className="workspace-header campus-header">
        <div><h1>校园服务</h1><p>经过核验的办事入口与公开活动</p></div>
        <form className="campus-search" onSubmit={(event) => { event.preventDefault(); submitSearch(); }}>
          <Search size={17} aria-hidden="true" />
          <input
            aria-label={activeTab === "services" ? "搜索校园服务" : "搜索校园活动"}
            value={currentSearch}
            onChange={(event) => {
              if (activeTab === "services") setServiceSearch(event.target.value);
              else setActivitySearch(event.target.value);
            }}
            placeholder={activeTab === "services" ? "搜索办事入口" : "搜索公开活动"}
          />
          <button type="submit" disabled={currentLoading}>搜索</button>
        </form>
      </header>
      <div className="campus-toolbar">
        <Tabs.List className="tabs-list tabs-list--compact" aria-label="校园信息类型">
          <Tabs.Trigger value="services"><Landmark size={15} />办事入口</Tabs.Trigger>
          <Tabs.Trigger value="activities"><Sparkles size={15} />活动</Tabs.Trigger>
        </Tabs.List>
        <label className="compact-select">类别
          <select value={currentCategory} onChange={(event) => changeCategory(event.target.value)}>
            <option value="">全部</option>
            {currentCategories.map((category) => <option key={category} value={category}>{category}</option>)}
          </select>
        </label>
        {(currentSearch || currentCategory) && (
          <button className="text-command" type="button" onClick={clearFilters}>清除筛选</button>
        )}
      </div>
      <div className="tabs-scroll">
        <Tabs.Content value="services" className="campus-content" data-loading={serviceLoading}>
          {serviceLoading && !services ? <ServicesSkeleton /> : !services ? (
            <WorkspaceError message={serviceError ?? "校园入口暂不可用。"} onRetry={() => void loadServices(serviceSearch, serviceCategory)} />
          ) : (
            <>
              <div className="content-source-row">
                <span>{servicesFiltered ? `${allServices.length} 个匹配入口` : `${allServices.length} 个经过配置审核的入口`}</span>
                <SourceBadge source={services.source} />
              </div>
              {!servicesFiltered && featuredServices.length > 0 && (
                <section className="campus-featured" aria-labelledby="campus-featured-title">
                  <header className="campus-section-heading">
                    <div><span>QUICK ACCESS</span><h2 id="campus-featured-title">常用入口</h2></div>
                    <p>8 项高频校园服务</p>
                  </header>
                  <div className="launch-grid launch-grid--featured">
                    {featuredServices.map((service, index) => (
                      <LaunchTile
                        key={service.url}
                        index={index + 1}
                        title={service.name}
                        description={service.description}
                        category={service.category}
                        host={readableHost(service.url)}
                        href={service.url}
                        icon={serviceIcon(service)}
                      />
                    ))}
                  </div>
                </section>
              )}
              <section className="campus-directory" aria-labelledby="campus-directory-title">
                <header className="campus-section-heading campus-section-heading--directory">
                  <div><span>{servicesFiltered ? "SEARCH RESULT" : "DIRECTORY"}</span><h2 id="campus-directory-title">{servicesFiltered ? "筛选结果" : "完整分类目录"}</h2></div>
                  <p>{directoryServices.length} 个入口</p>
                </header>
                {directoryServices.length ? (
                  <div className="catalog-groups catalog-groups--tiles">
                    {serviceGroups.map(([category, items]) => {
                      const open = !mobileCatalog || servicesFiltered || openCategories.has(category);
                      return (
                        <section className="catalog-group catalog-group--tiles" data-open={open} key={category}>
                          <header className="catalog-group__heading">
                            <h3>{category}</h3>
                            <span>{items.length} 个入口</span>
                            <button
                              className="catalog-group__toggle"
                              type="button"
                              aria-label={`${open ? "收起" : "展开"}${category}`}
                              aria-expanded={open}
                              onClick={() => toggleCategory(category)}
                            >
                              <ChevronDown size={17} aria-hidden="true" />
                            </button>
                          </header>
                          <div className="launch-grid launch-grid--directory">
                            {items.map((service, index) => (
                              <LaunchTile
                                key={service.url}
                                index={index + 1}
                                title={service.name}
                                description={service.description}
                                category={service.category}
                                host={readableHost(service.url)}
                                href={service.url}
                                icon={serviceIcon(service)}
                              />
                            ))}
                          </div>
                        </section>
                      );
                    })}
                  </div>
                ) : <div className="empty-result">没有匹配的校园入口</div>}
              </section>
            </>
          )}
        </Tabs.Content>
        <Tabs.Content value="activities" className="campus-content" data-loading={activityLoading}>
          {activityLoading && !activities ? <WorkspaceLoading label="正在汇集校园公开活动" /> : !activities ? (
            <WorkspaceError message={activityError ?? "校园活动暂不可用。"} onRetry={() => void loadActivities(activitySearch, activityCategory)} />
          ) : (
            <>
              <div className="content-source-row">
                <span>{activitiesFiltered ? `${activities.items.length} 条匹配活动` : `${activities.items.length} 条公开活动`}</span>
                <SourceBadge source={activities.source} />
              </div>
              {activities.items.length ? (
                <div className="catalog-groups">
                  {activityGroups.map(([category, items]) => (
                    <section className="catalog-group" key={category}>
                      <header className="catalog-group__heading"><h3>{category}</h3><span>{items.length} 条活动</span></header>
                      <div className="catalog-rows">
                        {items.map((activity, index) => {
                          const url = typeof activity.url === "string" ? activity.url : "";
                          return (
                            <article className="activity-item catalog-row catalog-row--activity" key={String(activity.id ?? `${activityTitle(activity)}-${index}`)}>
                              <div className="activity-item__date"><CalendarRange size={16} /><span>{readableTime(activity.start_time || activity.deadline)}</span></div>
                              <div className="catalog-row__body"><h3>{activityTitle(activity)}</h3>{activity.description && <p>{String(activity.description)}</p>}</div>
                              <div className="activity-item__footer"><span><MapPin size={14} />{String(activity.location || "地点待核验")}</span></div>
                              {url ? <a href={url} target="_blank" rel="noreferrer"><ExternalLink size={16} /><span>来源</span></a> : <span className="catalog-row__unavailable">暂无链接</span>}
                            </article>
                          );
                        })}
                      </div>
                    </section>
                  ))}
                </div>
              ) : <div className="empty-result">暂无匹配的公开活动</div>}
              <Limitations items={activities.limitations} />
            </>
          )}
        </Tabs.Content>
      </div>
    </Tabs.Root>
  );
}
