import os
from pathlib import Path




# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-ukx_%8-7ssdqtc(k=sf$b=7fw#5yo=@6xdtjhidem9z-26$kvn'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = False

ALLOWED_HOSTS = ['localhost', '127.0.0.1']

LOGIN_URL = 'login'  
LOGIN_REDIRECT_URL = 'dashboard'  
LOGOUT_REDIRECT_URL = 'logout' 


# settings.py
INTERNAL_IPS = [
    '127.0.0.1',
]

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'mcms_app',
    'crispy_forms',
    'django.contrib.humanize',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

ROOT_URLCONF = 'mcms_project.urls'



TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            # os.path.join(BASE_DIR, 'templates')
            BASE_DIR / 'templates',
            BASE_DIR / 'mcms_app' / 'templates',  # Add this line if not present
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'mcms_project.wsgi.application'


DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'mcms_app',
        'USER': 'admin',
        'PASSWORD': '556622',
        'HOST': 'localhost',
        'PORT': '3306',
    }
}


AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'file': {
            'level': 'WARNING', # Captures WARNING, ERROR, CRITICAL messages
            'class': 'logging.FileHandler',
            'filename': BASE_DIR / 'django_app_errors.log', # Log file name and location
            'formatter': 'verbose',
        },
        'console': { # Optional: To also see logs in the server console
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'loggers': {
        'django': { # Catches Django's internal errors
            'handlers': ['file', 'console'], # Add 'console' for terminal output
            'level': 'WARNING', # Adjust level as needed (INFO, DEBUG)
            'propagate': True,
        },
        'myapp': { # Example: Your specific app's logger
            'handlers': ['file', 'console'],
            'level': 'INFO',
            'propagate': False, # Set to False if you don't want it to go to parent loggers
        },
    },
}

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


BASE_DIR = Path(__file__).resolve().parent.parent

STATIC_URL = '/static/'
STATICFILES_DIRS = [
    BASE_DIR / "mcms_app" / "static",
]
STATIC_ROOT = BASE_DIR / "staticfiles"


DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'mediafiles'