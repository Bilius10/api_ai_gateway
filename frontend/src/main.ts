import 'zone.js';
import { provideZoneChangeDetection } from '@angular/core';
import { bootstrapApplication } from '@angular/platform-browser';
import { provideHttpClient } from '@angular/common/http';
import { provideRouter } from '@angular/router';

import { AppComponent, routes } from './app/app';

bootstrapApplication(AppComponent, {
  providers: [provideZoneChangeDetection(), provideHttpClient(), provideRouter(routes)],
}).catch((error: unknown) => console.error(error));
