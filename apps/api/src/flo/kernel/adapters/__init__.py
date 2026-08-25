"""Provider adapters selected only at the kernel composition boundary."""

from flo.kernel.adapters.minio import MinioStorage, create_minio_storage
from flo.kernel.adapters.r2 import R2Storage, create_r2_storage
from flo.kernel.adapters.resend import ResendEmailSender
from flo.kernel.adapters.smtp import SmtpEmailSender

__all__ = [
    "MinioStorage",
    "R2Storage",
    "ResendEmailSender",
    "SmtpEmailSender",
    "create_minio_storage",
    "create_r2_storage",
]
