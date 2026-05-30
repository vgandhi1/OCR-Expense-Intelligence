import React, { useState } from 'react';
import client from '../api/client';
import { Upload as UploadIcon, Loader2, CheckCircle, XCircle } from 'lucide-react';

const POLL_MS = 600;
const POLL_MAX_ATTEMPTS = 120;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const Upload = ({ onUploadSuccess }) => {
    const [dragActive, setDragActive] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [success, setSuccess] = useState(false);

    const handleDrag = (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (e.type === "dragenter" || e.type === "dragover") {
            setDragActive(true);
        } else if (e.type === "dragleave") {
            setDragActive(false);
        }
    };

    const handleDrop = (e) => {
        e.preventDefault();
        e.stopPropagation();
        setDragActive(false);
        if (e.dataTransfer.files && e.dataTransfer.files[0]) {
            handleFile(e.dataTransfer.files[0]);
        }
    };

    const handleChange = (e) => {
        e.preventDefault();
        if (e.target.files && e.target.files[0]) {
            handleFile(e.target.files[0]);
        }
    };

    const pollJob = async (jobId) => {
        for (let i = 0; i < POLL_MAX_ATTEMPTS; i += 1) {
            const { data } = await client.get(`/receipts/jobs/${jobId}`);
            if (data.status === 'complete') {
                return;
            }
            if (data.status === 'failed') {
                throw new Error(data.error_message || 'Processing failed');
            }
            await sleep(POLL_MS);
        }
        throw new Error('Processing timed out');
    };

    const handleFile = async (file) => {
        setLoading(true);
        setError(null);
        setSuccess(false);

        const formData = new FormData();
        formData.append("file", file);

        try {
            const uploadRes = await client.post('/receipts/upload', formData, {
                headers: {
                    'Content-Type': 'multipart/form-data',
                },
            });
            const jobId = uploadRes.data?.job_id;
            if (!jobId) {
                throw new Error('Invalid server response');
            }
            await pollJob(jobId);
            setSuccess(true);
            if (onUploadSuccess) onUploadSuccess();
        } catch (err) {
            console.error(err);
            const msg =
                err.response?.data?.detail ||
                err.message ||
                'Upload failed. Please try again.';
            setError(typeof msg === 'string' ? msg : 'Upload failed. Please try again.');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="w-full max-w-md mx-auto mb-8">
            <form
                className={`relative p-8 border-2 border-dashed rounded-lg text-center transition-colors 
                    ${dragActive ? "border-blue-500 bg-blue-50" : "border-gray-300 bg-white"}
                    ${loading ? "opacity-50 pointer-events-none" : ""}
                `}
                onDragEnter={handleDrag}
                onDragLeave={handleDrag}
                onDragOver={handleDrag}
                onDrop={handleDrop}
                onClick={() => document.getElementById('file-upload').click()}
            >
                <input
                    type="file"
                    id="file-upload"
                    className="hidden"
                    accept="image/*"
                    onChange={handleChange}
                />

                <div className="flex flex-col items-center justify-center space-y-4">
                    {loading ? (
                        <Loader2 className="w-12 h-12 text-blue-500 animate-spin" />
                    ) : success ? (
                        <CheckCircle className="w-12 h-12 text-green-500" />
                    ) : (
                        <UploadIcon className="w-12 h-12 text-gray-400" />
                    )}

                    <div>
                        {loading ? (
                            <p className="text-lg font-medium text-gray-700">Processing receipt…</p>
                        ) : success ? (
                            <p className="text-lg font-medium text-green-600">Upload Successful!</p>
                        ) : (
                            <>
                                <p className="text-lg font-medium text-gray-700">
                                    Drop receipt image here
                                </p>
                                <p className="text-sm text-gray-500 mt-1">
                                    or click to select
                                </p>
                            </>
                        )}
                    </div>
                </div>
            </form>
            {error && (
                <div className="mt-4 p-4 bg-red-50 text-red-600 rounded-md flex items-center">
                    <XCircle className="w-5 h-5 mr-2" />
                    {error}
                </div>
            )}
        </div>
    );
};

export default Upload;
