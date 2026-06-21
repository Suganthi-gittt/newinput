import React, { useEffect, useState } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  ResponsiveContainer,
  ComposedChart,
} from 'recharts';
import {
  AlertTriangle,
  Clock,
  Target,
  Layers,
  CheckCircle,
  Zap,
  Calendar,
  Users,
  Upload,
  TrendingDown,
  TrendingUp,
  Shield,
  Loader2,
  X,
} from 'lucide-react';

const API_BASE_URL = 'https://crispy-system-v674rrp6j54qc6wrj-8000.app.github.dev/api';

const formatDate = (iso) => {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
};

const parseNumber = (value) => {
  return value != null && !Number.isNaN(value) ? value : null;
};

const statusClasses = {
  LOW: 'bg-emerald-500 text-emerald-900',
  MODERATE: 'bg-amber-500 text-amber-900',
  HIGH: 'bg-red-500 text-red-900',
  VERY_HIGH: 'bg-red-500 text-red-900',
  CRITICAL: 'bg-red-500 text-red-900',
};

const recommendationTypeColors = {
  RESOLVE_BLOCKER: 'bg-red-500 text-white',
  DESCOPE_WORK: 'bg-amber-500 text-slate-900',
  ADD_RESOURCE: 'bg-sky-500 text-white',
  REASSIGN_WORK: 'bg-violet-500 text-white',
  SPLIT_TASK: 'bg-emerald-500 text-white',
  CRITICAL_PATH_OPTIMIZATION: 'bg-orange-500 text-white',
};

export default function SprintWhispererDashboard() {
  const [sessionId, setSessionId] = useState(null);
  const [projectSummary, setProjectSummary] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadStatus, setUploadStatus] = useState('idle');
  const [uploadMessage, setUploadMessage] = useState('');
  const [dashboardLoading, setDashboardLoading] = useState(false);
  const [dashboardData, setDashboardData] = useState(null);
  const [toastMessage, setToastMessage] = useState(null);
  const [selectedRecommendationIds, setSelectedRecommendationIds] = useState([]);
  const [scenarioResult, setScenarioResult] = useState(null);
  const [scenarioLoading, setScenarioLoading] = useState(false);
  const [simulateModal, setSimulateModal] = useState({ open: false, loading: false, result: null, error: '' });
  const [activeStep, setActiveStep] = useState('project-health');

  const showToast = (message) => {
    if (!message) return;
    setToastMessage(message);
    window.setTimeout(() => {
      setToastMessage(null);
    }, 5000);
  };

  const safeJson = async (response) => {
    const text = await response.text();
    let json = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch (error) {
      throw new Error(response.statusText || 'Invalid JSON response');
    }
    if (!response.ok) {
      throw new Error(json?.message || response.statusText || 'API request failed');
    }
    if (json && json.success === false) {
      throw new Error(json.message || 'API indicated failure');
    }
    return json;
  };

  const apiFetch = async (url, options = {}) => {
    try {
      const response = await fetch(url, {
        ...options,
        headers: {
          ...(options.headers || {}),
          'Content-Type': 'application/json',
        },
      });
      return await safeJson(response);
    } catch (error) {
      throw new Error(error.message || 'Network error');
    }
  };

  const handleFileChange = (file) => {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.xlsx')) {
      showToast('Please select an .xlsx workbook.');
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      showToast('File must be 10MB or smaller.');
      return;
    }
    setSelectedFile(file);
  };

  const handleInputChange = (event) => {
    handleFileChange(event.target.files[0]);
  };

  const handleDrop = (event) => {
    event.preventDefault();
    const file = event.dataTransfer.files[0];
    handleFileChange(file);
  };

  const handleDragOver = (event) => {
    event.preventDefault();
  };

  const handleUpload = async () => {
    if (!selectedFile) return;
    setUploadStatus('uploading');
    setUploadMessage('Parsing workbook...');
    try {
      const formData = new FormData();
      formData.append('file', selectedFile);
      const response = await fetch(`${API_BASE_URL}/upload`, {
        method: 'POST',
        body: formData,
      });
      const json = await safeJson(response);
      const data = json.data || {};
      setProjectSummary(data.project_summary || null);
      setUploadStatus('success');
      window.setTimeout(() => {
        setSessionId(data.session_id);
      }, 1500);
    } catch (error) {
      setUploadStatus('error');
      setUploadMessage(error.message || 'Upload failed');
      showToast(error.message || 'Upload failed');
    }
  };

  const handleUseDemo = async () => {
    setUploadStatus('uploading');
    setUploadMessage('Loading demo...');
    try {
      const response = await apiFetch(`${API_BASE_URL}/demo/load`, {
        method: 'POST',
        body: JSON.stringify({}),
      });
      const data = response.data || {};
      setProjectSummary(data.project_summary || null);
      setUploadStatus('success');
      window.setTimeout(() => {
        setSessionId(data.session_id);
      }, 1500);
    } catch (error) {
      setUploadStatus('error');
      setUploadMessage(error.message || 'Demo load failed');
      showToast(error.message || 'Demo load failed');
    }
  };

  const handleResetDemo = async () => {
    if (!sessionId) return;
    setDashboardLoading(true);
    try {
      await apiFetch(`${API_BASE_URL}/demo/reset?session_id=${sessionId}`, { method: 'POST' });
      const response = await apiFetch(`${API_BASE_URL}/demo/load`, {
        method: 'POST',
        body: JSON.stringify({}),
      });
      const data = response.data || {};
      setProjectSummary(data.project_summary || null);
      setSessionId(data.session_id);
      setDashboardData(null);
      setScenarioResult(null);
      setSelectedRecommendationIds([]);
    } catch (error) {
      showToast(error.message || 'Reset demo failed');
    } finally {
      setDashboardLoading(false);
    }
  };

  const loadDashboard = async () => {
    if (!sessionId) return;
    setDashboardLoading(true);
    setDashboardData(null);
    setScenarioResult(null);
    setSelectedRecommendationIds([]);
    try {
      const forecastPromise = apiFetch(`${API_BASE_URL}/forecast?session_id=${sessionId}`)
        .then((json) => json.data)
        .catch((error) => {
          showToast(error.message);
          return null;
        });
      const riskPromise = apiFetch(`${API_BASE_URL}/risk?session_id=${sessionId}`)
        .then((json) => json.data)
        .catch((error) => {
          showToast(error.message);
          return null;
        });
      const recPromise = apiFetch(`${API_BASE_URL}/recommendations?session_id=${sessionId}&top_n=3`)
        .then((json) => json.data)
        .catch((error) => {
          showToast(error.message);
          return { recommendations: [] };
        });
      const monteCarloPromise = apiFetch(`${API_BASE_URL}/monte-carlo?session_id=${sessionId}`)
        .then((json) => json.data)
        .catch((error) => {
          showToast(error.message);
          return null;
        });
      const metricsPromise = apiFetch(`${API_BASE_URL}/metrics?session_id=${sessionId}`)
        .then((json) => json.data)
        .catch((error) => {
          showToast(error.message);
          return null;
        });

      const [forecastData, riskData, recData, monteCarloData, metricsData] = await Promise.all([
        forecastPromise,
        riskPromise,
        recPromise,
        monteCarloPromise,
        metricsPromise,
      ]);

      setDashboardData({
        forecast: forecastData?.forecast || null,
        risk: riskData?.risk_analysis || null,
        recommendations: recData?.recommendations || [],
        monteCarlo: monteCarloData?.monte_carlo || null,
        metrics: metricsData?.metrics || null,
        projectName:
          forecastData?.project_name ||
          riskData?.project_name ||
          recData?.project_name ||
          monteCarloData?.project_name ||
          metricsData?.project_name ||
          projectSummary?.project_name ||
          'Sprint Whisperer',
      });
    } catch (error) {
      showToast(error.message || 'Unable to load dashboard');
    } finally {
      setDashboardLoading(false);
    }
  };

  useEffect(() => {
    if (sessionId) {
      loadDashboard();
    }
  }, [sessionId]);

  const numberOrDash = (value) => {
    if (value == null) return '—';
    return typeof value === 'number' ? value.toLocaleString() : value;
  };

  const getSeverityColor = (value) => {
    if (value == null) return 'text-slate-200';
    if (value >= 70) return 'text-red-400';
    if (value >= 40) return 'text-amber-400';
    return 'text-emerald-400';
  };

  const getRiskBarColor = (score) => {
    if (score == null) return 'bg-slate-500';
    if (score >= 70) return 'bg-red-500';
    if (score >= 40) return 'bg-amber-500';
    return 'bg-emerald-500';
  };

  const delayDays = dashboardData?.forecast?.expected_delay_days != null ? Math.round(dashboardData.forecast.expected_delay_days) : null;
  const onTimePct = dashboardData?.monteCarlo?.on_time_probability != null ? Math.round(dashboardData.monteCarlo.on_time_probability * 100) : null;
  const riskScore = dashboardData?.risk?.overall_risk_score != null ? Math.round(dashboardData.risk.overall_risk_score) : null;
  const riskLevel = dashboardData?.risk?.overall_risk_level || null;
  const completionPct = dashboardData?.forecast?.completion_percentage != null ? Math.round(dashboardData.forecast.completion_percentage * 100) : null;
  const remainingHours = dashboardData?.forecast?.remaining_effort_hours != null ? Math.round(dashboardData.forecast.remaining_effort_hours) : null;
  const projectedVelocity = dashboardData?.forecast?.projected_velocity != null ? Math.round(dashboardData.forecast.projected_velocity) : null;
  const activeBlockers = Array.isArray(dashboardData?.risk?.top_risk_drivers)
    ? dashboardData.risk.top_risk_drivers.filter((d) => d.category === 'BLOCKER').length
    : 0;
  const healthColor = riskLevel === 'LOW' ? 'bg-emerald-500 text-emerald-900' : riskLevel === 'MODERATE' ? 'bg-amber-500 text-amber-900' : 'bg-red-500 text-red-900';

  const riskRadarData = [
    { subject: 'Schedule', value: dashboardData?.risk?.schedule_risk?.score ?? 0 },
    { subject: 'Resource', value: dashboardData?.risk?.resource_risk?.score ?? 0 },
    { subject: 'Dependency', value: dashboardData?.risk?.dependency_risk?.score ?? 0 },
    { subject: 'Scope', value: dashboardData?.risk?.scope_risk?.score ?? 0 },
  ];

  const delayBreakdown = dashboardData?.forecast?.delay_breakdown;
  const waterfallData = delayBreakdown
    ? [
        {
          name: 'Planned',
          planned: delayBreakdown.planned_window_days ?? 0,
          base: 0,
          spillover: 0,
          blocker: 0,
          total: 0,
        },
        {
          name: 'Base Work',
          planned: 0,
          base: delayBreakdown.remaining_days_base_work ?? 0,
          spillover: 0,
          blocker: 0,
          total: 0,
        },
        {
          name: 'Spillover',
          planned: 0,
          base: 0,
          spillover: delayBreakdown.remaining_days_spillover ?? 0,
          blocker: 0,
          total: 0,
        },
        {
          name: 'Blockers',
          planned: 0,
          base: 0,
          spillover: 0,
          blocker: delayBreakdown.remaining_days_blocker_loss ?? 0,
          total: 0,
        },
        {
          name: 'Forecast',
          planned: 0,
          base: 0,
          spillover: 0,
          blocker: 0,
          total: delayBreakdown.remaining_days_total ?? 0,
        },
      ]
    : [];

  const recommendationCards = dashboardData?.recommendations || [];

  const handleSimulateOpen = async (recommendation) => {
    setSimulateModal({ open: true, loading: true, result: null, error: '' });
    try {
      const response = await apiFetch(
        `${API_BASE_URL}/recommendations/simulate?session_id=${sessionId}`,
        {
          method: 'POST',
          body: JSON.stringify({ recommendation_id: recommendation.recommendation_id }),
        }
      );
      setSimulateModal({ open: true, loading: false, result: response.data || null, error: '' });
    } catch (error) {
      setSimulateModal({ open: true, loading: false, result: null, error: error.message || 'Simulation failed' });
    }
  };

  const handleScenarioToggle = (recommendationId) => {
    setSelectedRecommendationIds((current) => {
      if (current.includes(recommendationId)) {
        return current.filter((id) => id !== recommendationId);
      }
      return [...current, recommendationId];
    });
  };

  const handleRunScenario = async () => {
    if (!selectedRecommendationIds.length) return;
    setScenarioLoading(true);
    try {
      const response = await apiFetch(
        `${API_BASE_URL}/recommendations/scenario?session_id=${sessionId}`,
        {
          method: 'POST',
          body: JSON.stringify({ recommendation_ids: selectedRecommendationIds }),
        }
      );
      setScenarioResult(response.data || null);
    } catch (error) {
      showToast(error.message || 'Scenario simulation failed');
    } finally {
      setScenarioLoading(false);
    }
  };

  const handleSectionScroll = (sectionId, stepId) => {
    const el = document.getElementById(sectionId);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      setActiveStep(stepId);
    }
  };

  const getSimulatedState = () => {
    const data = scenarioResult?.scenario || scenarioResult || {};
    return {
      delayDays: data.after_delay_days ?? data.delay_days ?? null,
      onTimePct: data.probability != null ? Math.round(data.probability * 100) : data.on_time_probability != null ? Math.round(data.on_time_probability * 100) : null,
      riskScore: data.risk_score ?? null,
      delayReduction: data.delay_reduction ?? null,
      probabilityGain: data.probability_gain ?? null,
      riskReduction: data.risk_reduction ?? null,
    };
  };

  const simulatedState = getSimulatedState();

  const completedSprints = dashboardData?.metrics?.completed_sprints ?? projectSummary?.completed_sprints ?? 0;
  const totalSprints = dashboardData?.metrics?.total_sprints ?? projectSummary?.total_sprints ?? 6;
  const sprintNodes = Array.from({ length: totalSprints }, (_, index) => index + 1);
  const currentSprintIndex = Math.min(completedSprints + 1, totalSprints);

  const demoSteps = [
    { id: 'project-health', label: 'Project Health', target: 'section-hero' },
    { id: 'why-late', label: 'Why Late', target: 'section-why-late' },
    { id: 'actions', label: 'Actions', target: 'section-recommendations' },
    { id: 'simulator', label: 'Simulator', target: 'section-simulator' },
    { id: 'risk', label: 'Risk Analysis', target: 'section-risk' },
  ];

  const renderLoadingScreen = () => (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex items-center justify-center px-6">
      <div className="max-w-xl text-center">
        <div className="mb-8 text-sm uppercase tracking-[0.24em] text-violet-400">Sprint Whisperer</div>
        <div className="flex justify-center mb-6">
          <Loader2 className="h-16 w-16 animate-spin text-violet-400" />
        </div>
        <h1 className="text-3xl font-semibold">Loading project intelligence...</h1>
        <p className="mt-4 text-slate-400">
          Crunching forecast signals<span className="inline-flex animate-pulse">.</span>
          <span className="inline-flex animate-pulse delay-75">.</span>
          <span className="inline-flex animate-pulse delay-150">.</span>
        </p>
      </div>
    </div>
  );

  const uploadScreen = () => (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col items-center justify-center px-4 py-8">
      <div className="max-w-md w-full text-center mb-10">
        <div className="text-violet-300 uppercase tracking-[0.32em] text-xs">Sprint Whisperer</div>
        <h1 className="mt-4 text-4xl font-bold text-white">AI-Powered Sprint Forecasting & Recovery</h1>
        <p className="mt-3 text-slate-400">Upload your sprint workbook or use the demo path to launch the executive dashboard instantly.</p>
      </div>
      <div className="w-full max-w-md bg-slate-900 border border-slate-800 rounded-3xl p-8 shadow-2xl">
        <div
          className="rounded-3xl border-2 border-dashed border-slate-700 bg-slate-950/40 p-8 text-center cursor-pointer hover:border-violet-400 transition"
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onClick={() => document.getElementById('workbook-file-input')?.click()}
        >
          <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-3xl bg-slate-800 text-violet-400 mb-6">
            <Upload className="h-8 w-8" />
          </div>
          <h2 className="text-xl font-semibold text-white">Drop your Sprint Workbook here</h2>
          <p className="mt-2 text-slate-400">Accepts .xlsx files up to 10MB</p>
          <input id="workbook-file-input" type="file" accept=".xlsx" className="hidden" onChange={handleInputChange} />
        </div>
        {selectedFile ? (
          <div className="mt-6 rounded-2xl bg-slate-800 p-4 text-left text-slate-200">
            <div className="text-sm text-slate-400">Selected file</div>
            <div className="mt-1 text-sm font-medium">{selectedFile.name}</div>
          </div>
        ) : null}
        {uploadStatus === 'error' ? (
          <div className="mt-6 rounded-2xl bg-red-500/10 border border-red-500 px-4 py-3 text-sm text-red-200">
            {uploadMessage}
          </div>
        ) : null}
        {uploadStatus === 'success' ? (
          <div className="mt-6 rounded-2xl bg-emerald-500/10 border border-emerald-500 px-4 py-3 text-sm text-emerald-200 flex items-center justify-between">
            <span>Workbook analyzed successfully.</span>
            <CheckCircle className="h-5 w-5 text-emerald-400" />
          </div>
        ) : null}
        <button
          className="mt-8 w-full rounded-3xl bg-gradient-to-r from-violet-500 to-fuchsia-500 px-6 py-4 text-base font-semibold text-white shadow-lg shadow-violet-500/20 disabled:cursor-not-allowed disabled:opacity-60"
          disabled={!selectedFile || uploadStatus === 'uploading'}
          onClick={handleUpload}
        >
          {uploadStatus === 'uploading' ? 'Analyzing workbook...' : 'Analyze Project'}
        </button>
        {uploadStatus === 'uploading' ? (
          <div className="mt-4 flex items-center gap-3 text-sm text-slate-300">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span>{uploadMessage}</span>
          </div>
        ) : null}
      </div>
      <button
        className="mt-6 text-sm font-semibold uppercase tracking-[0.18em] text-violet-300 hover:text-white"
        onClick={handleUseDemo}
      >
        Use Demo Workbook
      </button>
    </div>
  );

  if (!sessionId) {
    return uploadScreen();
  }

  if (dashboardLoading || !dashboardData) {
    return renderLoadingScreen();
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 pb-28">
      <div className="max-w-[1600px] mx-auto px-6 py-10">
        <section id="section-hero" className="mb-12 rounded-[28px] border border-slate-800 bg-slate-900 px-10 py-10 shadow-xl shadow-slate-950/30">
          <div className="flex flex-col gap-8 xl:flex-row xl:items-center xl:justify-between">
            <div className="space-y-4">
              <div className="inline-flex items-center gap-2 rounded-full bg-violet-500/10 px-4 py-2 text-sm font-semibold text-violet-300">Sprint Whisperer</div>
              <h1 className="text-4xl font-bold text-white">{dashboardData.projectName || 'Sprint Whisperer'}</h1>
              <div className={`inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-semibold ${healthColor}`}>
                <span className="h-2.5 w-2.5 rounded-full animate-pulse bg-current" />
                {riskLevel || 'UNKNOWN'} RISK
              </div>
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div className="rounded-full bg-slate-800 px-6 py-5 text-center">
                <div className={`text-lg font-semibold ${delayDays != null ? (delayDays > 0 ? 'text-red-400' : 'text-emerald-400') : 'text-slate-300'}`}>
                  ⏱ {delayDays != null ? `${delayDays} Days Late` : '—'}
                </div>
              </div>
              <div className="rounded-full bg-slate-800 px-6 py-5 text-center">
                <div className={`text-lg font-semibold ${onTimePct != null ? getSeverityColor(onTimePct) : 'text-slate-300'}`}>
                  🎯 {onTimePct != null ? `${onTimePct}% On-Time` : '—'}
                </div>
              </div>
              <div className="rounded-full bg-slate-800 px-6 py-5 text-center">
                <div className={`text-lg font-semibold ${riskLevel === 'LOW' ? 'text-emerald-400' : riskLevel === 'MODERATE' ? 'text-amber-400' : 'text-red-400'}`}>
                  ⚠ {riskScore != null ? `${riskScore} Risk Score` : '—'}
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="mb-12 rounded-[28px] border border-slate-800 bg-slate-900 p-10 shadow-xl shadow-slate-950/30">
          <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-3">
              <div className="rounded-full bg-violet-500/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.24em] text-violet-300">AI</div>
              <div className="text-sm uppercase tracking-[0.24em] text-slate-400">Executive Intelligence</div>
            </div>
          </div>
          <p className="text-lg leading-8 text-slate-300">
            <span className="font-semibold text-white">{dashboardData.projectName || 'This project'}</span> is forecasted to finish <span className="font-semibold text-white">{delayDays != null ? `${Math.abs(delayDays)} days ${delayDays > 0 ? 'late' : delayDays < 0 ? 'early' : 'on time'}` : '—'}</span> with a <span className="font-semibold text-white">{onTimePct != null ? `${onTimePct}%` : '—'}</span> probability of meeting the release target. Overall risk is rated <span className="font-semibold text-white">{riskLevel || '—'}</span> with a score of <span className="font-semibold text-white">{riskScore != null ? `${riskScore}/100` : '—'}</span>. The project is <span className="font-semibold text-white">{completionPct != null ? `${completionPct}%` : '—'}</span> complete with <span className="font-semibold text-white">{remainingHours != null ? `${remainingHours}h` : '—'}</span> of work remaining at <span className="font-semibold text-white">{projectedVelocity != null ? `${projectedVelocity}h` : '—'}</span> per sprint velocity. {dashboardData.risk?.top_risk_drivers?.[0]?.description || 'Key risk drivers are being monitored closely.'}
          </p>
        </section>

        <section className="mb-12 grid gap-6 md:grid-cols-2 xl:grid-cols-4">
          {[
            {
              label: 'Risk Level',
              value: riskLevel || '—',
              subtitle: `${riskScore != null ? `${riskScore}/100` : '—'}`,
              icon: <AlertTriangle className="h-5 w-5 text-violet-400" />,
            },
            {
              label: 'Expected Delay',
              value: delayDays != null ? `${delayDays} Days` : '—',
              subtitle: `vs ${numberOrDash(dashboardData?.forecast?.planned_window_days)} day plan`,
              icon: <Clock className="h-5 w-5 text-sky-400" />,
            },
            {
              label: 'On-Time Chance',
              value: onTimePct != null ? `${onTimePct}%` : '—',
              subtitle: dashboardData?.monteCarlo?.on_time_risk_level || '—',
              icon: <Target className="h-5 w-5 text-emerald-400" />,
            },
            {
              label: 'Remaining Work',
              value: remainingHours != null ? `${remainingHours}h` : '—',
              subtitle: 'across remaining sprints',
              icon: <Layers className="h-5 w-5 text-cyan-400" />,
            },
            {
              label: 'Completion',
              value: completionPct != null ? `${completionPct}%` : '—',
              subtitle: 'of total effort',
              icon: <CheckCircle className="h-5 w-5 text-emerald-400" />,
            },
            {
              label: 'Team Velocity',
              value: projectedVelocity != null ? `${projectedVelocity}h` : '—',
              subtitle: 'per sprint (projected)',
              icon: <Zap className="h-5 w-5 text-amber-400" />,
            },
            {
              label: 'Schedule Risk',
              value: dashboardData?.risk?.schedule_risk?.score != null ? `${dashboardData.risk.schedule_risk.score}/100` : '—',
              subtitle: dashboardData?.risk?.schedule_risk?.reasons?.[0] || '—',
              icon: <Calendar className="h-5 w-5 text-sky-400" />,
            },
            {
              label: 'Resource Risk',
              value: dashboardData?.risk?.resource_risk?.score != null ? `${dashboardData.risk.resource_risk.score}/100` : '—',
              subtitle: dashboardData?.risk?.resource_risk?.reasons?.[0] || '—',
              icon: <Users className="h-5 w-5 text-violet-400" />,
            },
          ].map((card, index) => (
            <div key={index} className="rounded-3xl border border-slate-800 bg-slate-900 p-6 shadow-lg shadow-slate-950/20">
              <div className="flex items-center gap-3 text-slate-400 text-xs uppercase tracking-[0.2em]">
                {card.icon}
                <span>{card.label}</span>
              </div>
              <div className="mt-4 text-3xl font-bold text-white">{card.value}</div>
              <div className="mt-2 text-sm text-slate-400">{card.subtitle}</div>
            </div>
          ))}
        </section>

        <section id="section-why-late" className="mb-12 rounded-[28px] border border-slate-800 bg-slate-900 p-10 shadow-xl shadow-slate-950/30">
          <div className="mb-8 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 className="text-2xl font-bold text-white">Why Are We Late?</h2>
              <p className="mt-2 text-slate-400">Additive breakdown of forecast delay</p>
            </div>
          </div>
          {delayBreakdown ? (
            <div className="grid gap-8 xl:grid-cols-[1.5fr_1fr]">
              <div className="h-[360px] w-full rounded-3xl bg-slate-950/80 p-4">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={waterfallData} margin={{ top: 24, right: 16, left: 8, bottom: 0 }}>
                    <CartesianGrid stroke="#334155" vertical={false} />
                    <XAxis dataKey="name" tick={{ fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                    <Tooltip contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', color: '#f8fafc' }} />
                    <Bar dataKey="planned" stackId="a" fill="#3b82f6" radius={[12, 12, 0, 0]} />
                    <Bar dataKey="base" stackId="a" fill="#f97316" radius={[12, 12, 0, 0]} />
                    <Bar dataKey="spillover" stackId="a" fill="#ef4444" radius={[12, 12, 0, 0]} />
                    <Bar dataKey="blocker" stackId="a" fill="#b91c1c" radius={[12, 12, 0, 0]} />
                    <Bar dataKey="total" stackId="b" fill="#8b5cf6" radius={[12, 12, 0, 0]} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <div className="grid gap-4">
                {[
                  {
                    label: '📦 Base Work',
                    value: numberOrDash(delayBreakdown.remaining_days_base_work) + ' days',
                    subtitle: 'at projected velocity',
                  },
                  {
                    label: '🌊 Spillover',
                    value: numberOrDash(delayBreakdown.remaining_days_spillover) + ' days',
                    subtitle: 'of unplanned overflow',
                  },
                  {
                    label: '🚧 Blockers',
                    value: numberOrDash(delayBreakdown.remaining_days_blocker_loss) + ' days',
                    subtitle: 'velocity loss impact',
                  },
                ].map((item) => (
                  <div key={item.label} className="rounded-3xl border border-slate-800 bg-slate-950/70 p-6">
                    <div className="text-sm font-semibold text-white">{item.label}</div>
                    <div className="mt-3 text-3xl font-bold text-white">{item.value}</div>
                    <div className="mt-2 text-sm text-slate-400">{item.subtitle}</div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="rounded-3xl border border-slate-800 bg-slate-950/70 p-10 text-center text-slate-400">Breakdown data unavailable</div>
          )}
        </section>

        <section id="section-risk" className="mb-12 grid gap-6 xl:grid-cols-[1.4fr_1fr]">
          <div className="rounded-[28px] border border-slate-800 bg-slate-900 p-8 shadow-xl shadow-slate-950/20">
            <div className="mb-6 flex items-center justify-between">
              <div>
                <h3 className="text-2xl font-bold text-white">Risk Radar</h3>
                <p className="text-slate-400">A birds-eye view of risk exposure</p>
              </div>
            </div>
            <div className="h-[360px]">
              <ResponsiveContainer width="100%" height="100%">
                <RadarChart data={riskRadarData} outerRadius="80%">
                  <PolarGrid stroke="#334155" />
                  <PolarAngleAxis dataKey="subject" tick={{ fill: '#cbd5e1', fontSize: 12 }} />
                  <PolarRadiusAxis tick={false} axisLine={false} />
                  <Radar dataKey="value" stroke="#ef4444" fill="rgba(239,68,68,0.35)" fillOpacity={1} />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          </div>
          <div className="space-y-4">
            {[
              { label: 'Schedule Risk', key: 'schedule_risk' },
              { label: 'Resource Risk', key: 'resource_risk' },
              { label: 'Dependency Risk', key: 'dependency_risk' },
              { label: 'Scope Risk', key: 'scope_risk' },
            ].map((item) => {
              const score = dashboardData?.risk?.[item.key]?.score;
              const reason = dashboardData?.risk?.[item.key]?.reasons?.[0] || 'No reason available';
              return (
                <div key={item.key} className="rounded-3xl border border-slate-800 bg-slate-900 p-6 shadow-xl shadow-slate-950/20">
                  <div className="flex items-center justify-between gap-4">
                    <div className="text-sm uppercase tracking-[0.24em] text-slate-400">{item.label}</div>
                    <div className="text-xl font-bold text-white">{score != null ? `${score}` : '—'}</div>
                  </div>
                  <div className="mt-4 h-3 rounded-full bg-slate-800">
                    <div className={`${getRiskBarColor(score)} h-3 rounded-full`} style={{ width: `${score != null ? Math.min(100, score) : 0}%` }} />
                  </div>
                  <div className="mt-3 text-sm text-slate-400">{reason}</div>
                </div>
              );
            })}
          </div>
          <div className="xl:col-span-2 rounded-[28px] border border-slate-800 bg-slate-900 p-8 shadow-xl shadow-slate-950/20">
            <h3 className="text-2xl font-bold text-white">Top Risk Drivers</h3>
            <div className="mt-6 space-y-4">
              {(dashboardData?.risk?.top_risk_drivers || []).slice(0, 4).map((driver, index) => (
                <div key={index} className="rounded-3xl border border-slate-800 bg-slate-950/80 p-5">
                  <div className="flex items-center justify-between gap-4">
                    <span className="rounded-full bg-slate-800 px-3 py-1 text-xs uppercase tracking-[0.24em] text-slate-400">{driver.category || 'UNKNOWN'}</span>
                    <span className="text-xs text-slate-500">{driver.impact || ''}</span>
                  </div>
                  <div className="mt-3 text-base font-semibold text-white">{driver.title || driver.description || 'Risk driver'}</div>
                  <div className="mt-2 text-sm text-slate-400 truncate">{driver.description || 'No additional detail available.'}</div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section id="section-recommendations" className="mb-12 rounded-[28px] border border-slate-800 bg-slate-900 p-8 shadow-xl shadow-slate-950/20">
          <div className="mb-8 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-3xl font-bold text-white">Leadership Actions</h2>
              <p className="text-slate-400">Ranked by impact on delivery confidence</p>
            </div>
          </div>
          {recommendationCards.length === 0 ? (
            <div className="rounded-3xl border border-slate-800 bg-slate-950/80 p-10 text-center text-slate-400">No recommendations generated</div>
          ) : (
            <div className="grid gap-6 lg:grid-cols-3">
              {recommendationCards.map((rec) => {
                const typeClass = recommendationTypeColors[rec.type] || 'bg-slate-700 text-white';
                return (
                  <div key={rec.recommendation_id} className="rounded-3xl border border-slate-800 bg-slate-950/80 p-6">
                    <div className="mb-4 flex items-center justify-between gap-3">
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.24em] ${typeClass}`}>{rec.type || 'RECOMMENDATION'}</span>
                      <button
                        className="rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:bg-slate-800"
                        onClick={() => handleSimulateOpen(rec)}
                      >
                        Simulate
                      </button>
                    </div>
                    <div className="text-xl font-semibold text-white">{rec.action || 'Action unavailable'}</div>
                    <div className="mt-3 text-sm leading-6 text-slate-400">{rec.reason || 'No reason provided.'}</div>
                    <div className="mt-6 grid gap-3 text-sm text-slate-300 sm:grid-cols-3">
                      <div className="rounded-3xl bg-slate-800 p-3 text-emerald-400">−{numberOrDash(rec.expected_delay_gain_days)} days</div>
                      <div className="rounded-3xl bg-slate-800 p-3 text-emerald-400">+{numberOrDash(Math.round((rec.expected_probability_gain ?? 0) * 100))}% on-time</div>
                      <div className="rounded-3xl bg-slate-800 p-3 text-emerald-400">−{numberOrDash((rec.expected_risk_reduction ?? 0).toFixed ? Number(rec.expected_risk_reduction).toFixed(1) : rec.expected_risk_reduction)} risk</div>
                    </div>
                    <div className="mt-6 flex flex-wrap gap-2 text-xs uppercase tracking-[0.24em] text-slate-300">
                      <span className={`rounded-full px-3 py-1 ${rec.implementation_effort === 'Low' ? 'bg-emerald-500 text-slate-900' : rec.implementation_effort === 'Medium' ? 'bg-amber-500 text-slate-900' : 'bg-red-500 text-white'}`}>Effort: {rec.implementation_effort || '—'}</span>
                      <span className="rounded-full bg-slate-800 px-3 py-1">Confidence: {rec.confidence || '—'}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>

        <section id="section-simulator" className="mb-12 grid gap-6 xl:grid-cols-3">
          <div className="rounded-[28px] border border-slate-800 bg-slate-900 p-8 shadow-xl shadow-slate-950/20">
            <div className="text-sm uppercase tracking-[0.24em] text-slate-400">Current State</div>
            <div className="mt-6 space-y-6">
              <div className="rounded-3xl bg-slate-950/80 p-6">
                <div className="text-sm text-slate-400">Delay</div>
                <div className="mt-3 text-4xl font-semibold text-red-400">{delayDays != null ? `${delayDays} days` : '—'}</div>
              </div>
              <div className="rounded-3xl bg-slate-950/80 p-6">
                <div className="text-sm text-slate-400">On-Time</div>
                <div className="mt-3 text-4xl font-semibold text-emerald-400">{onTimePct != null ? `${onTimePct}%` : '—'}</div>
              </div>
              <div className="rounded-3xl bg-slate-950/80 p-6">
                <div className="text-sm text-slate-400">Risk Score</div>
                <div className="mt-3 text-4xl font-semibold text-white">{riskScore != null ? riskScore : '—'}</div>
              </div>
            </div>
            <div className="mt-6 rounded-3xl bg-slate-950/80 px-5 py-4 text-sm uppercase tracking-[0.24em] text-slate-400">BASELINE</div>
          </div>
          <div className="rounded-[28px] border border-slate-800 bg-slate-900 p-8 shadow-xl shadow-slate-950/20">
            <div className="text-sm uppercase tracking-[0.24em] text-slate-400">Select Actions</div>
            <div className="mt-6 space-y-4">
              {recommendationCards.map((rec) => (
                <label key={rec.recommendation_id} className="flex items-start gap-3 rounded-3xl border border-slate-800 bg-slate-950/80 p-4">
                  <input
                    type="checkbox"
                    checked={selectedRecommendationIds.includes(rec.recommendation_id)}
                    onChange={() => handleScenarioToggle(rec.recommendation_id)}
                    className="mt-1 h-4 w-4 rounded border-slate-700 bg-slate-900 text-violet-500 focus:ring-violet-500"
                  />
                  <div>
                    <div className="text-sm font-semibold text-white">{rec.action || 'Action unavailable'}</div>
                    <div className="mt-1 text-sm text-slate-400">{rec.type || 'Unknown type'}</div>
                  </div>
                </label>
              ))}
            </div>
            <button
              className="mt-6 w-full rounded-3xl bg-gradient-to-r from-violet-500 to-fuchsia-500 px-6 py-4 text-base font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
              disabled={!selectedRecommendationIds.length || scenarioLoading}
              onClick={handleRunScenario}
            >
              {scenarioLoading ? 'Running scenario...' : 'Run Scenario'}
            </button>
          </div>
          <div className="rounded-[28px] border border-slate-800 bg-slate-900 p-8 shadow-xl shadow-slate-950/20">
            <div className="flex items-center justify-between gap-4">
              <div>
                <div className="text-sm uppercase tracking-[0.24em] text-slate-400">Simulated State</div>
                <div className="mt-2 text-xs text-slate-500">Run scenario to see results</div>
              </div>
              <span className="rounded-full bg-slate-800 px-3 py-1 text-xs text-slate-300">SIMULATED</span>
            </div>
            <div className="mt-6 space-y-6">
              <div className="rounded-3xl bg-slate-950/80 p-6">
                <div className="text-sm text-slate-400">Delay</div>
                <div className="mt-3 text-4xl font-semibold text-red-400">{simulatedState.delayDays != null ? `${simulatedState.delayDays} days` : '—'}</div>
              </div>
              <div className="rounded-3xl bg-slate-950/80 p-6">
                <div className="text-sm text-slate-400">On-Time</div>
                <div className="mt-3 text-4xl font-semibold text-emerald-400">{simulatedState.onTimePct != null ? `${simulatedState.onTimePct}%` : '—'}</div>
              </div>
              <div className="rounded-3xl bg-slate-950/80 p-6">
                <div className="text-sm text-slate-400">Risk Score</div>
                <div className="mt-3 text-4xl font-semibold text-white">{simulatedState.riskScore != null ? simulatedState.riskScore : '—'}</div>
              </div>
            </div>
            {simulatedState.delayReduction != null || simulatedState.probabilityGain != null || simulatedState.riskReduction != null ? (
              <div className="mt-6 rounded-3xl border border-slate-800 bg-slate-950/80 p-5 text-sm text-emerald-300">
                <div className="flex items-center justify-between gap-3">
                  <span className="flex items-center gap-2 text-emerald-400"><TrendingDown className="h-4 w-4" /> −{numberOrDash(simulatedState.delayReduction)} days</span>
                  <span className="flex items-center gap-2 text-emerald-400"><TrendingUp className="h-4 w-4" /> +{numberOrDash(Math.round((simulatedState.probabilityGain ?? 0) * 100))}%</span>
                  <span className="flex items-center gap-2 text-emerald-400"><TrendingDown className="h-4 w-4" /> −{numberOrDash(simulatedState.riskReduction)}</span>
                </div>
              </div>
            ) : null}
          </div>
        </section>

        <section className="mb-12 rounded-[28px] border border-slate-800 bg-slate-900 p-8 shadow-xl shadow-slate-950/20 overflow-hidden">
          <div className="mb-8 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-3xl font-bold text-white">Sprint Timeline</h2>
              <p className="text-slate-400">Visual sprint progress from completed work to forecast target.</p>
            </div>
          </div>
          <div className="overflow-x-auto pb-4">
            <div className="flex min-w-[720px] items-center gap-6 px-2">
              {sprintNodes.map((sprint) => {
                const completed = sprint <= completedSprints;
                const inProgress = sprint === currentSprintIndex && sprint > completedSprints;
                const atRisk = sprint > currentSprintIndex && delayDays > 0;
                return (
                  <div key={sprint} className="flex min-w-[120px] flex-col items-center gap-3 text-center">
                    <div className="relative flex h-16 w-16 items-center justify-center rounded-full border border-slate-700 bg-slate-950">
                      <div className={`absolute inset-0 ${inProgress ? 'animate-pulse rounded-full bg-sky-500/20' : ''}`} />
                      <div className={`relative flex h-10 w-10 items-center justify-center rounded-full ${completed ? 'bg-emerald-500 text-slate-950' : inProgress ? 'bg-sky-500 text-white' : atRisk ? 'border-2 border-red-500 bg-slate-950 text-red-500' : 'border border-slate-700 bg-slate-950 text-slate-400'}`}>
                        {completed ? <CheckCircle className="h-5 w-5" /> : sprint}
                      </div>
                    </div>
                    <div className="text-sm text-slate-300">Sprint {sprint}</div>
                  </div>
                );
              })}
              <div className="flex min-w-[240px] flex-col gap-4 rounded-3xl border border-amber-500/20 bg-slate-950/80 p-6">
                <div className="text-sm uppercase tracking-[0.24em] text-amber-300">Target</div>
                <div className="text-lg font-semibold text-white">{formatDate(dashboardData?.forecast?.target_end_date)}</div>
                <div className="text-sm text-slate-400">Forecast</div>
                <div className="text-lg font-semibold text-white">{formatDate(dashboardData?.forecast?.expected_finish_date)}</div>
                <div className="rounded-3xl bg-slate-900 px-4 py-3 text-sm text-slate-300">{delayDays != null ? `${delayDays}d gap` : '—'}</div>
              </div>
            </div>
          </div>
        </section>

        <section className="mb-12 grid gap-6 xl:grid-cols-2">
          <div className="rounded-[28px] border border-slate-800 bg-slate-900 p-8 shadow-xl shadow-slate-950/20">
            <div className="mb-5 flex items-center justify-between gap-4">
              <div>
                <h2 className="text-2xl font-bold text-white">Probabilistic Forecast</h2>
                <p className="text-slate-400">10,000 simulation distribution</p>
              </div>
            </div>
            <div className="space-y-4 text-sm text-slate-300">
              <div className="flex justify-between border-b border-slate-800 pb-3">
                <span>Best Case (P10)</span>
                <span>{formatDate(dashboardData?.monteCarlo?.best_case_finish_date)}</span>
              </div>
              <div className="flex justify-between border-b border-slate-800 pb-3">
                <span>Most Likely (P50)</span>
                <span>{formatDate(dashboardData?.monteCarlo?.most_likely_finish_date)}</span>
              </div>
              <div className="flex justify-between border-b border-slate-800 pb-3">
                <span>P80 Confidence</span>
                <span>{formatDate(dashboardData?.monteCarlo?.p80_finish_date)}</span>
              </div>
              <div className="flex justify-between pb-3">
                <span>P90 Finish Date</span>
                <span>{formatDate(dashboardData?.monteCarlo?.p90_finish_date)}</span>
              </div>
            </div>
            <div className="mt-8 rounded-3xl bg-slate-950/80 p-6 text-center">
              <div className="text-sm uppercase tracking-[0.24em] text-slate-400">On-Time Probability</div>
              <div className="mt-4 text-5xl font-semibold text-white">{onTimePct != null ? `${onTimePct}%` : '—'}</div>
              <div className="mt-2 text-sm text-slate-400">{dashboardData?.monteCarlo?.on_time_risk_level || '—'}</div>
              <div className="mt-6 overflow-hidden rounded-full bg-slate-800 py-3">
                <div className={`h-4 rounded-full ${getRiskBarColor(onTimePct)}`} style={{ width: `${onTimePct != null ? onTimePct : 0}%` }} />
              </div>
            </div>
          </div>
          <div className="rounded-[28px] border border-slate-800 bg-slate-900 p-8 shadow-xl shadow-slate-950/20">
            <h2 className="text-2xl font-bold text-white">Why Deterministic vs Probabilistic Differ</h2>
            <p className="mt-4 leading-8 text-slate-300">
              {dashboardData?.forecast?.forecast_vs_montecarlo_note ||
                'The deterministic forecast applies worst-case assumptions. Monte Carlo samples the full uncertainty range including optimistic scenarios. Both figures are correct — they answer different questions.'}
            </p>
          </div>
        </section>
      </div>

      <div className="fixed inset-x-0 bottom-0 z-50 border-t border-slate-800 bg-slate-900 px-6 py-3 shadow-2xl shadow-slate-950/20">
        <div className="mx-auto flex max-w-[1600px] flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap items-center gap-4">
            <span className="rounded-full bg-violet-500/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.24em] text-violet-300">DEMO MODE</span>
            <div className="flex flex-wrap gap-2">
              {demoSteps.map((step) => (
                <button
                  key={step.id}
                  className={`rounded-full px-4 py-2 text-sm font-semibold transition ${activeStep === step.id ? 'bg-violet-500 text-slate-950' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}`}
                  onClick={() => handleSectionScroll(step.target, step.id)}
                >
                  {step.label}
                </button>
              ))}
            </div>
          </div>
          <button
            className="rounded-3xl bg-slate-800 px-5 py-3 text-sm font-semibold text-slate-100 hover:bg-slate-700"
            onClick={handleResetDemo}
          >
            Reset Demo
          </button>
        </div>
      </div>

      {toastMessage ? (
        <div className="fixed right-6 top-6 z-50 w-80 rounded-3xl border border-slate-800 bg-slate-950 p-4 shadow-2xl shadow-slate-950/40">
          <div className="flex items-start justify-between gap-4">
            <div className="space-y-1">
              <div className="text-sm font-semibold text-white">Error</div>
              <p className="text-sm text-slate-300">{toastMessage}</p>
            </div>
            <button onClick={() => setToastMessage(null)} className="text-slate-400 hover:text-slate-200">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
      ) : null}

      {simulateModal.open ? (
        <div className="fixed inset-0 z-60 flex items-center justify-center bg-slate-950/80 p-6">
          <div className="w-full max-w-3xl rounded-3xl border border-slate-800 bg-slate-900 p-8 shadow-2xl shadow-slate-950/60">
            <div className="mb-6 flex items-center justify-between gap-4">
              <div>
                <h3 className="text-2xl font-bold text-white">Recommendation Simulation</h3>
                <p className="text-sm text-slate-400">Preview the before and after impact of the selected action.</p>
              </div>
              <button onClick={() => setSimulateModal({ open: false, loading: false, result: null, error: '' })} className="rounded-full bg-slate-800 p-2 text-slate-300 hover:text-white">
                <X className="h-5 w-5" />
              </button>
            </div>
            {simulateModal.loading ? (
              <div className="flex flex-col items-center justify-center gap-4 rounded-3xl border border-slate-800 bg-slate-950/80 p-10 text-slate-300">
                <Loader2 className="h-10 w-10 animate-spin text-violet-400" />
                <p>Simulating recommendation impact...</p>
              </div>
            ) : simulateModal.error ? (
              <div className="rounded-3xl border border-red-500 bg-red-500/10 p-6 text-red-200">{simulateModal.error}</div>
            ) : simulateModal.result ? (
              <div className="grid gap-6 md:grid-cols-2">
                <div className="rounded-3xl border border-slate-800 bg-slate-950/80 p-6">
                  <div className="text-sm uppercase tracking-[0.24em] text-slate-400">Before</div>
                  <div className="mt-4 space-y-3 text-sm text-slate-300">
                    <div>Delay: {numberOrDash(simulateModal.result.before?.delay_days ?? simulateModal.result.before_delay_days)} days</div>
                    <div>On-Time: {simulateModal.result.before?.probability != null ? `${Math.round(simulateModal.result.before.probability * 100)}%` : '—'}</div>
                    <div>Risk Score: {numberOrDash(simulateModal.result.before?.risk_score ?? simulateModal.result.before_risk_score)}</div>
                  </div>
                </div>
                <div className="rounded-3xl border border-slate-800 bg-slate-950/80 p-6">
                  <div className="text-sm uppercase tracking-[0.24em] text-slate-400">After</div>
                  <div className="mt-4 space-y-3 text-sm text-slate-300">
                    <div>Delay: {numberOrDash(simulateModal.result.after?.delay_days ?? simulateModal.result.after_delay_days)}</div>
                    <div>On-Time: {simulateModal.result.after?.probability != null ? `${Math.round(simulateModal.result.after.probability * 100)}%` : '—'}</div>
                    <div>Risk Score: {numberOrDash(simulateModal.result.after?.risk_score ?? simulateModal.result.after_risk_score)}</div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="rounded-3xl border border-slate-800 bg-slate-950/80 p-6 text-slate-300">Simulation data unavailable.</div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
