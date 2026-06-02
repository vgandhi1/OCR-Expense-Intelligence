import React, { useEffect, useState } from 'react';
import client from '../api/client';
import { CATEGORIES, currentMonth } from '../constants';
import { Loader2, Plus, Wallet } from 'lucide-react';

const monthLabel = (ym) => {
    const [y, m] = ym.split('-').map(Number);
    return new Date(y, m - 1, 1).toLocaleString('default', { month: 'long', year: 'numeric' });
};

// Bar colour mirrors the spec: green under 85%, amber up to budget, red over.
const barColor = (actual, limit) => {
    if (limit <= 0) return 'bg-blue-400';
    const pct = (actual / limit) * 100;
    if (actual > limit) return 'bg-rose-500';
    if (pct > 85) return 'bg-amber-500';
    return 'bg-emerald-500';
};

const BudgetPanel = ({ refreshTrigger }) => {
    const [month] = useState(currentMonth());
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(true);
    const [form, setForm] = useState({ category: 'Groceries', limit_amount: '' });
    const [saving, setSaving] = useState(false);

    const fetchData = async () => {
        try {
            const res = await client.get(`/analytics/budget-progress/${month}`);
            setRows(res.data);
        } catch (err) {
            console.error('Error fetching budget progress:', err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchData();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [refreshTrigger, month]);

    const saveBudget = async (e) => {
        e.preventDefault();
        const amount = Number(form.limit_amount);
        if (!Number.isFinite(amount) || amount < 0) return;
        setSaving(true);
        try {
            await client.post('/expenses/budgets', {
                category: form.category,
                limit_amount: amount,
                month,
            });
            setForm({ ...form, limit_amount: '' });
            await fetchData();
        } catch (err) {
            console.error('Error saving budget:', err);
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="bg-white p-6 rounded-lg shadow-sm border border-gray-100">
            <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
                <h3 className="text-lg font-semibold text-gray-700 flex items-center gap-2">
                    <Wallet size={18} className="text-blue-500" />
                    Budgets <span className="text-sm font-normal text-gray-400">· {monthLabel(month)}</span>
                </h3>

                <form onSubmit={saveBudget} className="flex items-center gap-2">
                    <select
                        value={form.category}
                        onChange={(e) => setForm({ ...form, category: e.target.value })}
                        className="border rounded-lg px-2 py-1.5 text-sm bg-white focus:ring-2 focus:ring-blue-500 focus:outline-none"
                    >
                        {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                    <input
                        type="number"
                        step="0.01"
                        min="0"
                        value={form.limit_amount}
                        onChange={(e) => setForm({ ...form, limit_amount: e.target.value })}
                        placeholder="Limit $"
                        className="w-24 border rounded-lg px-2 py-1.5 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
                    />
                    <button
                        type="submit"
                        disabled={saving}
                        className="inline-flex items-center gap-1 px-3 py-1.5 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                    >
                        {saving ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                        Set
                    </button>
                </form>
            </div>

            {loading ? (
                <div className="flex justify-center py-6"><Loader2 className="animate-spin text-gray-400" /></div>
            ) : rows.length === 0 ? (
                <p className="text-sm text-gray-400 py-4 text-center">
                    No spend or budgets this month. Set a category limit to start tracking.
                </p>
            ) : (
                <div className="space-y-4">
                    {rows.map((item) => {
                        const overBudget = item.limit > 0 && item.actual > item.limit;
                        const pct = item.limit > 0 ? Math.min((item.actual / item.limit) * 100, 100) : 100;
                        return (
                            <div key={item.category}>
                                <div className="flex justify-between text-sm mb-1">
                                    <span className="font-medium text-gray-700">
                                        {item.category}
                                        {overBudget && <span className="ml-2 text-xs text-rose-600 font-semibold">over budget</span>}
                                    </span>
                                    <span className="text-gray-500">
                                        ${item.actual.toFixed(2)} / {item.limit > 0 ? `$${item.limit.toFixed(2)}` : 'No limit'}
                                    </span>
                                </div>
                                <div className="w-full bg-gray-100 rounded-full h-2.5">
                                    <div
                                        className={`h-2.5 rounded-full transition-all duration-500 ${barColor(item.actual, item.limit)}`}
                                        style={{ width: `${pct}%` }}
                                    />
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
};

export default BudgetPanel;
