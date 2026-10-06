import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/l10n.dart';
import '../state/session.dart';

/// Sign in, or create the account. The mobile app has one entry point for both
/// roles: a provider is a customer who also owns an organization, so the
/// "provider" choice adds one field (the org name) rather than a second flow.
class AuthScreen extends StatefulWidget {
  const AuthScreen({super.key});

  @override
  State<AuthScreen> createState() => _AuthScreenState();
}

class _AuthScreenState extends State<AuthScreen> {
  final _formKey = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _fullName = TextEditingController();
  final _phone = TextEditingController();
  final _orgName = TextEditingController();

  bool _register = false;
  bool _asProvider = false;
  bool _busy = false;
  bool _obscure = true;
  String? _error;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    _fullName.dispose();
    _phone.dispose();
    _orgName.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    final session = context.read<Session>();
    final failure = _register
        ? await session.register(
            email: _email.text,
            password: _password.text,
            fullName: _fullName.text,
            phone: _phone.text,
            organizationName: _asProvider ? _orgName.text : null,
          )
        : await session.login(_email.text, _password.text);
    if (!mounted) return;
    setState(() {
      _busy = false;
      _error = failure;
    });
  }

  @override
  Widget build(BuildContext context) {
    final l = AppLocalizations.of(context);
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(
        title: Text(l.t(_register ? 'register' : 'login')),
        actions: [
          IconButton(
            onPressed: () => context.read<LocaleController>().toggle(),
            icon: const Icon(Icons.translate),
            tooltip: l.t('language'),
          ),
        ],
      ),
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(20),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 460),
              child: Form(
                key: _formKey,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      l.t('tagline'),
                      textAlign: TextAlign.center,
                      style: theme.textTheme.bodyMedium?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                    ),
                    const SizedBox(height: 22),
                    if (_register) ...[
                      _field(
                        _fullName,
                        'full_name',
                        validator: (v) => (v ?? '').trim().length < 2
                            ? l.t('something_wrong')
                            : null,
                      ),
                      const SizedBox(height: 12),
                      _segmentRole(),
                      const SizedBox(height: 12),
                    ],
                    _field(
                      _email,
                      'email',
                      keyboard: TextInputType.emailAddress,
                      validator: (v) =>
                          (v ?? '').contains('@') && (v ?? '').contains('.')
                          ? null
                          : l.t('something_wrong'),
                    ),
                    const SizedBox(height: 12),
                    TextFormField(
                      controller: _password,
                      obscureText: _obscure,
                      decoration: InputDecoration(
                        labelText: l.t('password'),
                        border: const OutlineInputBorder(),
                        suffixIcon: IconButton(
                          onPressed: () => setState(() => _obscure = !_obscure),
                          icon: Icon(
                            _obscure
                                ? Icons.visibility_off_outlined
                                : Icons.visibility_outlined,
                          ),
                        ),
                      ),
                      validator: (v) =>
                          (v ?? '').length < 8 ? l.t('something_wrong') : null,
                      onFieldSubmitted: (_) => _submit(),
                    ),
                    if (_register) ...[
                      const SizedBox(height: 12),
                      _field(_phone, 'phone', keyboard: TextInputType.phone),
                      if (_asProvider) ...[
                        const SizedBox(height: 12),
                        _field(
                          _orgName,
                          'org_name',
                          validator: (v) => (v ?? '').trim().length < 2
                              ? l.t('something_wrong')
                              : null,
                        ),
                      ],
                    ],
                    if (_error != null) ...[
                      const SizedBox(height: 16),
                      Material(
                        color: theme.colorScheme.errorContainer,
                        borderRadius: BorderRadius.circular(10),
                        child: Padding(
                          padding: const EdgeInsets.all(12),
                          child: Text(
                            _error!,
                            style: TextStyle(
                              color: theme.colorScheme.onErrorContainer,
                            ),
                          ),
                        ),
                      ),
                    ],
                    const SizedBox(height: 20),
                    FilledButton(
                      onPressed: _busy ? null : _submit,
                      child: _busy
                          ? const SizedBox(
                              height: 20,
                              width: 20,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : Text(l.t(_register ? 'register' : 'login')),
                    ),
                    TextButton(
                      onPressed: _busy
                          ? null
                          : () => setState(() {
                              _register = !_register;
                              _error = null;
                            }),
                      child: Text(
                        l.t(_register ? 'have_account' : 'no_account'),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _segmentRole() {
    final l = AppLocalizations.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(l.t('i_am'), style: Theme.of(context).textTheme.bodySmall),
        const SizedBox(height: 6),
        SegmentedButton<bool>(
          segments: [
            ButtonSegment(value: false, label: Text(l.t('role_customer'))),
            ButtonSegment(value: true, label: Text(l.t('role_provider'))),
          ],
          selected: {_asProvider},
          showSelectedIcon: false,
          onSelectionChanged: (s) => setState(() => _asProvider = s.first),
        ),
      ],
    );
  }

  Widget _field(
    TextEditingController controller,
    String labelKey, {
    TextInputType? keyboard,
    String? Function(String?)? validator,
  }) {
    final l = AppLocalizations.of(context);
    return TextFormField(
      controller: controller,
      keyboardType: keyboard,
      decoration: InputDecoration(
        labelText: l.t(labelKey),
        border: const OutlineInputBorder(),
      ),
      validator: validator,
    );
  }
}
