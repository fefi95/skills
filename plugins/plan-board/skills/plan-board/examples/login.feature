Feature: User login
  Let returning users sign in with email and password.

  Background:
    Given the user database is seeded

  @id:login-form @file:create:src/ui/LoginForm.tsx
  Scenario: Show the login form
    When the user opens /login
    Then an email field and a password field are shown

  @id:login-api @depends:login-form @file:create:src/api/login.ts @file:edit:src/api/index.ts
  Scenario: Sign in with correct details
    Given a user "ann@example.com" exists
    When they submit the right password
    Then they are sent to the dashboard
    And a session cookie is set
    When they submit a wrong password
    Then they see "Wrong email or password"

  Scenario Outline: Lock out
    Given a user has failed <n> times
    Then the account is locked
    Examples:
      | n |
      | 5 |
