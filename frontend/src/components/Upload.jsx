import React, { useState } from 'react';
import client from '../api/client';
import { Upload as UploadIcon, Loader2, CheckCircle, XCircle } from 'lucide-react';

const POLL_MS = 600;
const POLL_MAX_ATTEMPTS = 120;

const ACCEPT = 'image/*,application/pdf,.pdf';
const MAX_FILE_BYTES = 15 * 1024 * 1024; // 15 MB

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const isSupported = (file) =>
    file.type.startsWith('image/') ||
    file.type === 'application/pdf' ||
    /\.pdf$/i.test(file.name);

const Upload = ({ onUploadSuccess }) => {
    const [dragActive, setDragActive] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [success, setSuccess] = useState(false);
    const [fileName, setFileName] = useState(null);

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
        // Reset so selecting the same file again still fires onChange.
        e.target.value = '';
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
        setError(null);
        setSuccess(false);
        setFileName(file.name);

        if (!isSupported(file)) {
            setError('Unsupported file type. Upload an image (JPG, PNG) or a PDF.');
            return;
        }
        if (file.size > MAX_FILE_BYTES) {
            setError('File is too large. Maximum size is 15 MB.');
            return;
        }

        setLoading(true);

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
        <div className="w-full">
            <form
                className={`relative flex items-center gap-4 px-6 py-5 border-2 border-dashed rounded-xl transition-colors cursor-pointer
                    ${dragActive ? "border-blue-500 bg-blue-50" : "border-gray-300 bg-white hover:border-gray-400"}
                    ${loading ? "opacity-60 pointer-events-none" : ""}
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
                    accept={ACCEPT}
                    onChange={handleChange}
                />

                <div className="shrink-0">
                    {loading ? (
                        <Loader2 className="w-9 h-9 text-blue-500 animate-spin" />
                    ) : success ? (
                        <CheckCircle className="w-9 h-9 text-green-500" />
                    ) : (
                        <UploadIcon className="w-9 h-9 text-gray-400" />
                    )}
                </div>

                <div className="flex-1 min-w-0 text-left">
                    {loading ? (
                        <>
                            <p className="text-base font-medium text-gray-700">Processing receipt…</p>
                            {fileName && (
                                <p className="text-sm text-gray-500 truncate">{fileName}</p>
                            )}
                        </>
                    ) : success ? (
                        <>
                            <p className="text-base font-medium text-green-600">Upload successful!</p>
                            <p className="text-sm text-gray-500">Drop or click to upload another</p>
                        </>
                    ) : (
                        <>
                            <p className="text-base font-medium text-gray-700">
                                Drop a receipt here, or click to browse
                            </p>
                            <p className="text-sm text-gray-500">
                                Supports JPG, PNG or PDF &middot; up to 15 MB
                            </p>
                        </>
                    )}
                </div>

                <span className="hidden sm:inline-flex shrink-0 items-center px-4 py-2 rounded-lg bg-gray-900 text-white text-sm font-medium">
                    Select file
                </span>
            </form>
            {error && (
                <div className="mt-3 p-3 bg-red-50 text-red-600 rounded-lg flex items-center text-sm">
                    <XCircle className="w-5 h-5 mr-2 shrink-0" />
                    {error}
                </div>
            )}
        </div>
    );
};

export default Upload;
