"""Provider adapters selected only at the kernel composition boundary."""

from flo.kernel.adapters.r2 import R2Storage, create_r2_storage
from flo.kernel.adapters.resend import ResendEmailSender
from flo.kernel.adapters.s3_local import LocalS3Storage, create_local_s3_storage
from flo.kernel.adapters.smtp import SmtpEmailSender

__all__ = [
    "LocalS3Storage",
    "R2Storage",
    "ResendEmailSender",
    "SmtpEmailSender",
    "create_local_s3_storage",
    "create_r2_storage",
]
