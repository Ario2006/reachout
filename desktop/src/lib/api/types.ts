import type { components } from './schema'

type S = components['schemas']

export type AppSettings = S['AppSettings']
export type SettingsResponse = S['SettingsResponse']
export type SettingsUpdate = S['SettingsUpdate']
export type SetupStatus = S['SetupStatus']
export type Check = S['CheckOut']
export type JobOut = S['JobOut']
export type GmailStatus = S['GmailStatus']
export type GmailTestResult = S['GmailTestResult']
export type SuppressionEntry = S['SuppressionEntry']
export type HealthOut = S['HealthOut']
export type ModelsOut = S['ModelsOut']
