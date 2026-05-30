import React, { useEffect, useState } from 'react';
import client from '../api/client';
import { Loader2, Pencil, Trash2, Check, X, ChevronDown, ChevronRight, ListTree } from 'lucide-react';

const CATEGORIES = [
    'Groceries', 'Dining', 'Transport', 'Shopping',
    'Utilities', 'Entertainment', 'Health', 'Uncategorized',
];

const categoryClass = (category) =>
    category === 'Groceries' ? 'bg-green-100 text-green-800' :
        category === 'Dining' ? 'bg-orange-100 text-orange-800' :
            category === 'Transport' ? 'bg-blue-100 text-blue-800' :
                'bg-gray-100 text-gray-800';

const toDateInput = (value) => {
    if (!value) return '';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 10);
};

// The API serializes the Mongo id as `_id` (FastAPI by_alias); fall back to `id`.
const receiptId = (receipt) => receipt._id || receipt.id;

const ItemizedBill = ({ items }) => {
    const sorted = [...items].sort((a, b) => (b.amount ?? 0) - (a.amount ?? 0));
    const max = sorted.length ? (sorted[0].amount ?? 0) : 0;
    const sum = sorted.reduce((acc, it) => acc + (it.amount ?? 0), 0);

    return (
        <div className="px-6 py-4 bg-gray-50">
            <div className="text-xs uppercase tracking-wide text-gray-500 mb-2">
                Itemized bill — {sorted.length} item{sorted.length === 1 ? '' : 's'} ·
                most expensive first
            </div>
            <table className="w-full text-sm">
                <tbody>
                    {sorted.map((item, idx) => {
                        const isTop = idx === 0 && max > 0;
                        const pct = max > 0 ? Math.round(((item.amount ?? 0) / max) * 100) : 0;
                        return (
                            <tr key={`${item.description}-${idx}`} className={isTop ? 'bg-red-50' : ''}>
                                <td className="py-1.5 pr-3 w-6 text-gray-400">{idx + 1}</td>
                                <td className="py-1.5 pr-3">
                                    <span className={isTop ? 'font-semibold text-red-700' : 'text-gray-800'}>
                                        {item.description}
                                    </span>
                                    {item.qty > 1 && <span className="text-gray-400"> ×{item.qty}</span>}
                                    {isTop && (
                                        <span className="ml-2 px-1.5 py-0.5 text-[10px] rounded-full bg-red-100 text-red-700 align-middle">
                                            most expensive
                                        </span>
                                    )}
                                </td>
                                <td className="py-1.5 pr-3 w-1/3">
                                    <div className="h-1.5 rounded bg-gray-200">
                                        <div
                                            className={`h-1.5 rounded ${isTop ? 'bg-red-400' : 'bg-blue-300'}`}
                                            style={{ width: `${pct}%` }}
                                        />
                                    </div>
                                </td>
                                <td className={`py-1.5 text-right tabular-nums ${isTop ? 'font-semibold text-red-700' : 'text-gray-700'}`}>
                                    ${(item.amount ?? 0).toFixed(2)}
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
                <tfoot>
                    <tr className="border-t border-gray-200">
                        <td />
                        <td className="py-1.5 text-gray-500">Items total</td>
                        <td />
                        <td className="py-1.5 text-right font-semibold text-gray-800 tabular-nums">${sum.toFixed(2)}</td>
                    </tr>
                </tfoot>
            </table>
        </div>
    );
};

const ReceiptsList = ({ refreshTrigger, onChange }) => {
    const [receipts, setReceipts] = useState([]);
    const [loading, setLoading] = useState(true);
    const [editingId, setEditingId] = useState(null);
    const [draft, setDraft] = useState({});
    const [busyId, setBusyId] = useState(null);
    const [expandedId, setExpandedId] = useState(null);
    const [error, setError] = useState('');

    const fetchReceipts = async () => {
        try {
            const res = await client.get('/receipts/');
            setReceipts(res.data);
        } catch (err) {
            console.error('Error fetching receipts:', err);
            setError('Could not load receipts.');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchReceipts();
    }, [refreshTrigger]);

    const startEdit = (receipt) => {
        setError('');
        setEditingId(receiptId(receipt));
        setDraft({
            merchant_name: receipt.merchant_name || '',
            category: receipt.category || 'Uncategorized',
            total_amount: receipt.total_amount ?? '',
            date: toDateInput(receipt.date),
        });
    };

    const cancelEdit = () => {
        setEditingId(null);
        setDraft({});
    };

    const saveEdit = async (id) => {
        setBusyId(id);
        setError('');
        try {
            const payload = {
                merchant_name: draft.merchant_name?.trim() || null,
                category: draft.category || null,
                total_amount: draft.total_amount === '' ? null : Number(draft.total_amount),
                date: draft.date ? new Date(draft.date).toISOString() : null,
            };
            await client.patch(`/receipts/${id}`, payload);
            cancelEdit();
            onChange?.();
            await fetchReceipts();
        } catch (err) {
            console.error('Error updating receipt:', err);
            setError('Could not save changes.');
        } finally {
            setBusyId(null);
        }
    };

    const deleteReceipt = async (id) => {
        if (!window.confirm('Delete this receipt? This cannot be undone.')) return;
        setBusyId(id);
        setError('');
        try {
            await client.delete(`/receipts/${id}`);
            onChange?.();
            await fetchReceipts();
        } catch (err) {
            console.error('Error deleting receipt:', err);
            setError('Could not delete receipt.');
        } finally {
            setBusyId(null);
        }
    };

    const itemize = async (id) => {
        setBusyId(id);
        setError('');
        try {
            await client.post(`/receipts/${id}/itemize`);
            await fetchReceipts();
            setExpandedId(id);
        } catch (err) {
            console.error('Error itemizing receipt:', err);
            setError('Could not generate itemized bill.');
        } finally {
            setBusyId(null);
        }
    };

    if (loading) {
        return <div className="flex justify-center p-8"><Loader2 className="animate-spin text-gray-400" /></div>;
    }

    return (
        <div className="bg-white shadow rounded-lg overflow-hidden">
            <h2 className="px-6 py-4 text-xl font-semibold border-b bg-gray-50 text-gray-700">Recent Receipts</h2>
            {error && <div className="px-6 py-2 text-sm text-red-600 bg-red-50 border-b">{error}</div>}
            {receipts.length === 0 ? (
                <div className="p-8 text-center text-gray-500">No receipts found. Upload one to get started.</div>
            ) : (
                <div className="overflow-x-auto">
                    <table className="w-full text-left">
                        <thead className="bg-gray-50 text-gray-600 uppercase text-xs">
                            <tr>
                                <th className="px-6 py-3 w-6"></th>
                                <th className="px-6 py-3">Date</th>
                                <th className="px-6 py-3">Merchant</th>
                                <th className="px-6 py-3">Category</th>
                                <th className="px-6 py-3 text-right">Total</th>
                                <th className="px-6 py-3 text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-200">
                            {receipts.map((receipt) => {
                                const id = receiptId(receipt);
                                const isEditing = editingId === id;
                                const isBusy = busyId === id;
                                const items = receipt.items || [];
                                const hasItems = items.length > 0;
                                const isExpanded = expandedId === id;
                                return (
                                    <React.Fragment key={id}>
                                        <tr className="hover:bg-gray-50">
                                            <td className="px-6 py-4 text-gray-400">
                                                {hasItems ? (
                                                    <button
                                                        onClick={() => setExpandedId(isExpanded ? null : id)}
                                                        title={isExpanded ? 'Hide items' : 'View items'}
                                                        className="p-1 rounded hover:bg-gray-100"
                                                    >
                                                        {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                                                    </button>
                                                ) : null}
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                                                {isEditing ? (
                                                    <input
                                                        type="date"
                                                        value={draft.date}
                                                        onChange={(e) => setDraft({ ...draft, date: e.target.value })}
                                                        className="border rounded px-2 py-1 text-sm"
                                                    />
                                                ) : (
                                                    receipt.date ? new Date(receipt.date).toLocaleDateString() : 'N/A'
                                                )}
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap font-medium text-gray-900">
                                                {isEditing ? (
                                                    <input
                                                        type="text"
                                                        value={draft.merchant_name}
                                                        onChange={(e) => setDraft({ ...draft, merchant_name: e.target.value })}
                                                        className="border rounded px-2 py-1 text-sm w-40"
                                                        placeholder="Merchant"
                                                    />
                                                ) : (
                                                    receipt.merchant_name || 'Unknown Merchant'
                                                )}
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                                                {isEditing ? (
                                                    <select
                                                        value={draft.category}
                                                        onChange={(e) => setDraft({ ...draft, category: e.target.value })}
                                                        className="border rounded px-2 py-1 text-sm"
                                                    >
                                                        {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
                                                    </select>
                                                ) : (
                                                    <span className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${categoryClass(receipt.category)}`}>
                                                        {receipt.category || 'Uncategorized'}
                                                    </span>
                                                )}
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap text-sm text-right font-semibold text-gray-900">
                                                {isEditing ? (
                                                    <input
                                                        type="number"
                                                        step="0.01"
                                                        min="0"
                                                        value={draft.total_amount}
                                                        onChange={(e) => setDraft({ ...draft, total_amount: e.target.value })}
                                                        className="border rounded px-2 py-1 text-sm w-24 text-right"
                                                        placeholder="0.00"
                                                    />
                                                ) : (
                                                    receipt.total_amount != null ? `$${receipt.total_amount.toFixed(2)}` : 'N/A'
                                                )}
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap text-right">
                                                <div className="flex items-center justify-end gap-2">
                                                    {isBusy ? (
                                                        <Loader2 size={18} className="animate-spin text-gray-400" />
                                                    ) : isEditing ? (
                                                        <>
                                                            <button onClick={() => saveEdit(id)} title="Save" className="p-1.5 rounded text-green-600 hover:bg-green-50">
                                                                <Check size={18} />
                                                            </button>
                                                            <button onClick={cancelEdit} title="Cancel" className="p-1.5 rounded text-gray-500 hover:bg-gray-100">
                                                                <X size={18} />
                                                            </button>
                                                        </>
                                                    ) : (
                                                        <>
                                                            {!hasItems && (
                                                                <button
                                                                    onClick={() => itemize(id)}
                                                                    title="Generate itemized bill"
                                                                    className="p-1.5 rounded text-purple-600 hover:bg-purple-50"
                                                                >
                                                                    <ListTree size={18} />
                                                                </button>
                                                            )}
                                                            <button onClick={() => startEdit(receipt)} title="Edit" className="p-1.5 rounded text-blue-600 hover:bg-blue-50">
                                                                <Pencil size={18} />
                                                            </button>
                                                            <button onClick={() => deleteReceipt(id)} title="Delete" className="p-1.5 rounded text-red-600 hover:bg-red-50">
                                                                <Trash2 size={18} />
                                                            </button>
                                                        </>
                                                    )}
                                                </div>
                                            </td>
                                        </tr>
                                        {isExpanded && hasItems && (
                                            <tr>
                                                <td colSpan={6} className="p-0 border-b border-gray-200">
                                                    <ItemizedBill items={items} />
                                                </td>
                                            </tr>
                                        )}
                                    </React.Fragment>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
};

export default ReceiptsList;
