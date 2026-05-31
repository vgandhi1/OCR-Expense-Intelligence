import React, { useEffect, useState } from 'react';
import client from '../api/client';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, Legend } from 'recharts';
import { Loader2, DollarSign, TrendingUp } from 'lucide-react';

const COLORS = ['#0088FE', '#00C49F', '#FFBB28', '#FF8042', '#8884d8'];
// Distinct palette for category slices so they read clearly against the merchant pie.
const CATEGORY_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6', '#64748b'];

const Dashboard = ({ refreshTrigger }) => {
    const [monthlyData, setMonthlyData] = useState([]);
    const [merchantData, setMerchantData] = useState([]);
    const [categoryData, setCategoryData] = useState([]);
    const [loading, setLoading] = useState(true);

    const fetchData = async () => {
        try {
            const [monthlyRes, merchantRes, categoryRes] = await Promise.all([
                client.get('/analytics/monthly'),
                client.get('/analytics/merchant'),
                client.get('/analytics/category'),
            ]);
            setMonthlyData(monthlyRes.data);
            setMerchantData(merchantRes.data);
            setCategoryData(categoryRes.data);
        } catch (err) {
            console.error("Error fetching analytics:", err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchData();
    }, [refreshTrigger]);

    if (loading) return <div className="flex justify-center p-10"><Loader2 className="animate-spin" /></div>;

    const totalSpend = monthlyData.reduce((acc, curr) => acc + curr.value, 0);

    return (
        <div className="space-y-6">
            {/* Stats Cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="bg-white p-6 rounded-lg shadow-sm border border-gray-100 flex items-center">
                    <div className="p-3 bg-blue-50 rounded-full mr-4">
                        <DollarSign className="w-6 h-6 text-blue-500" />
                    </div>
                    <div>
                        <p className="text-sm text-gray-500 font-medium">Total Detected Spend</p>
                        <h3 className="text-2xl font-bold text-gray-800">${totalSpend.toFixed(2)}</h3>
                    </div>
                </div>
                <div className="bg-white p-6 rounded-lg shadow-sm border border-gray-100 flex items-center">
                    <div className="p-3 bg-green-50 rounded-full mr-4">
                        <TrendingUp className="w-6 h-6 text-green-500" />
                    </div>
                    <div>
                        <p className="text-sm text-gray-500 font-medium">Top Merchant</p>
                        <h3 className="text-2xl font-bold text-gray-800">
                            {merchantData.length > 0 ? merchantData[0].name : "N/A"}
                        </h3>
                    </div>
                </div>
            </div>

            {/* Monthly Spend Bar Chart — full width */}
            <div className="bg-white p-6 rounded-lg shadow-sm border border-gray-100">
                <h3 className="text-lg font-semibold text-gray-700 mb-4">Monthly Spending</h3>
                <div className="h-64">
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={monthlyData}>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} />
                            <XAxis dataKey="name" axisLine={false} tickLine={false} />
                            <YAxis axisLine={false} tickLine={false} />
                            <Tooltip
                                contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
                                formatter={(value) => [`$${value.toFixed(2)}`, 'Spend']}
                            />
                            <Bar dataKey="value" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                        </BarChart>
                    </ResponsiveContainer>
                </div>
            </div>

            {/* Distribution Pies */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

                {/* Merchant Distribution Pie Chart */}
                <div className="bg-white p-6 rounded-lg shadow-sm border border-gray-100">
                    <h3 className="text-lg font-semibold text-gray-700 mb-4">Top Merchants</h3>
                    <div className="h-64">
                        {merchantData.length === 0 ? (
                            <div className="h-full flex items-center justify-center text-sm text-gray-400">No data yet</div>
                        ) : (
                            <ResponsiveContainer width="100%" height="100%">
                                <PieChart>
                                    <Pie
                                        data={merchantData}
                                        cx="50%"
                                        cy="50%"
                                        innerRadius={60}
                                        outerRadius={80}
                                        paddingAngle={5}
                                        dataKey="value"
                                    >
                                        {merchantData.map((entry, index) => (
                                            <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                                        ))}
                                    </Pie>
                                    <Tooltip formatter={(value) => [`$${value.toFixed(2)}`, 'Spend']} />
                                    <Legend />
                                </PieChart>
                            </ResponsiveContainer>
                        )}
                    </div>
                </div>

                {/* Category Distribution Pie Chart */}
                <div className="bg-white p-6 rounded-lg shadow-sm border border-gray-100">
                    <h3 className="text-lg font-semibold text-gray-700 mb-4">Spending by Category</h3>
                    <div className="h-64">
                        {categoryData.length === 0 ? (
                            <div className="h-full flex items-center justify-center text-sm text-gray-400">No data yet</div>
                        ) : (
                            <ResponsiveContainer width="100%" height="100%">
                                <PieChart>
                                    <Pie
                                        data={categoryData}
                                        cx="50%"
                                        cy="50%"
                                        outerRadius={80}
                                        paddingAngle={2}
                                        dataKey="value"
                                        nameKey="name"
                                        label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                                        labelLine={false}
                                    >
                                        {categoryData.map((entry, index) => (
                                            <Cell key={`cat-${index}`} fill={CATEGORY_COLORS[index % CATEGORY_COLORS.length]} />
                                        ))}
                                    </Pie>
                                    <Tooltip formatter={(value) => [`$${value.toFixed(2)}`, 'Spend']} />
                                    <Legend />
                                </PieChart>
                            </ResponsiveContainer>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
};

export default Dashboard;
