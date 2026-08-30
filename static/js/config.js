/* UWITZ CARDS — shared config */
window.UWITZ_CONFIG = {
  irys: {
    authorizeUrl: 'https://irys.uwz/oauth/authorize',
    tokenUrl:    'https://irys.uwz/oauth/token',
    clientId:    'YOUR_IRYS_CLIENT_ID',
    redirectUri: window.location.origin + '/dashboard/login',
    scope:       'openid profile email',
  },
  oidc: {
    providers: {
      entraid: {
        name: 'Entra ID',
        clientId: 'bf993ac3-0ca5-4172-9617-0e8851d5de1d',
        authorizeUrl: 'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize',
        redirectUri: window.location.origin + '/dashboard/login',
        scope: 'openid profile email User.Read',
        tenant: '7625c8c5-0680-4ccc-8840-dc993791d475',
      },
    },
  },
};
